"""Room registry for workspace WebSocket connections.

Each workspace is a *room*; every open socket in a room receives broadcasts.
Presence tracks who is connected (deduplicated by user, since one user may have
several tabs open) plus their last-known cursor and the surface they are viewing.

By default this registry is in-memory and single-process. When ``REDIS_URL`` is
configured it additionally fans broadcasts out over Redis pub/sub and tracks
presence in Redis, so several app processes can serve the same workspace behind
a load balancer; the rest of the app talks only to :class:`ConnectionManager`
either way.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import WebSocket

from app.config import settings

# All processes publish/subscribe on one channel; each frame carries its
# workspace id and the id of the process that originated it.
_CHANNEL = "collabspace:broadcast"


def _presence_key(workspace_id: str) -> str:
    return f"collabspace:presence:{workspace_id}"


def _members_key(workspace_id: str) -> str:
    return f"collabspace:members:{workspace_id}"


@dataclass
class Connection:
    """A single WebSocket plus a lock that serialises concurrent sends to it."""

    ws: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(message)


@dataclass
class Member:
    """One user in a room, tracked across all of their open sockets/tabs."""

    user_id: str
    display_name: str
    color: str
    connections: dict[WebSocket, Connection] = field(default_factory=dict)
    cursor: dict | None = None
    surface: str | None = None

    def public(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "color": self.color,
            "cursor": self.cursor,
            "surface": self.surface,
        }


class ConnectionManager:
    """Tracks rooms and fans messages out to their connections."""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, Member]] = {}
        self._lock = asyncio.Lock()
        # This process's identity, so it can ignore its own published frames.
        self._id = uuid4().hex
        self._redis = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None

    async def startup(self) -> None:
        """Connect to Redis and start the subscriber loop, if REDIS_URL is set."""
        if not settings.redis_url:
            return
        import redis.asyncio as aioredis  # optional dep, imported only when enabled

        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(_CHANNEL)
        self._reader_task = asyncio.create_task(self._reader())

    async def shutdown(self) -> None:
        """Tear down the subscriber task and Redis connections."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(BaseException):
                await self._reader_task
            self._reader_task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(_CHANNEL)
                await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    async def connect(self, workspace_id: str, ws: WebSocket, user) -> None:
        """Accept ``ws`` and register it under ``user`` in ``workspace_id``."""
        await ws.accept()
        async with self._lock:
            room = self._rooms.setdefault(workspace_id, {})
            member = room.get(user.id)
            if member is None:
                member = Member(user.id, user.display_name, user.color)
                room[user.id] = member
            member.connections[ws] = Connection(ws)
        if self._redis is not None:
            await self._redis.hset(
                _members_key(workspace_id),
                user.id,
                json.dumps({"display_name": user.display_name, "color": user.color}),
            )
            await self._redis.hincrby(_presence_key(workspace_id), user.id, 1)

    async def disconnect(self, workspace_id: str, ws: WebSocket, user_id: str) -> bool:
        """Drop one socket. Return ``True`` if the user has now fully left the room.

        With Redis, "fully left" means gone from *every* process, decided by the
        shared per-user connection counter; in-memory it means this process held
        the user's last socket for the room.
        """
        async with self._lock:
            room = self._rooms.get(workspace_id)
            removed = bool(room and user_id in room and ws in room[user_id].connections)
            local_last = False
            if removed:
                member = room[user_id]
                member.connections.pop(ws, None)
                if not member.connections:
                    local_last = True
                    del room[user_id]
                    if not room:
                        self._rooms.pop(workspace_id, None)
        if self._redis is None:
            return local_last
        if not removed:
            return False
        remaining = await self._redis.hincrby(_presence_key(workspace_id), user_id, -1)
        if remaining <= 0:
            await self._redis.hdel(_presence_key(workspace_id), user_id)
            await self._redis.hdel(_members_key(workspace_id), user_id)
            return True
        return False

    async def roster(self, workspace_id: str) -> list[dict]:
        """The public presence list for a room (one entry per connected user)."""
        if self._redis is None:
            room = self._rooms.get(workspace_id, {})
            return [m.public() for m in room.values()]
        raw = await self._redis.hgetall(_members_key(workspace_id))
        local = self._rooms.get(workspace_id, {})
        roster: list[dict] = []
        for user_id, meta in raw.items():
            member = local.get(user_id)
            if member is not None:
                roster.append(member.public())  # live cursor/surface for local users
            else:
                info = json.loads(meta)
                roster.append(
                    {
                        "user_id": user_id,
                        "display_name": info.get("display_name", "Someone"),
                        "color": info.get("color", "#6366f1"),
                        "cursor": None,
                        "surface": None,
                    }
                )
        return roster

    def set_cursor(
        self, workspace_id: str, user_id: str, cursor: dict | None, surface: str | None
    ) -> None:
        member = self._rooms.get(workspace_id, {}).get(user_id)
        if member is not None:
            member.cursor = cursor
            member.surface = surface

    async def broadcast(
        self, workspace_id: str, message: dict, *, exclude: WebSocket | None = None
    ) -> None:
        """Send ``message`` to every socket in the room except ``exclude``.

        Local sockets are delivered directly; when Redis is enabled the frame is
        also published so other processes deliver it to *their* local sockets.
        ``exclude`` only applies here — the excluded socket always lives on this
        process, so remote deliveries need no exclusion.
        """
        await self._deliver_local(workspace_id, message, exclude=exclude)
        if self._redis is not None:
            payload = json.dumps(
                {"origin": self._id, "workspace_id": workspace_id, "message": message}
            )
            with contextlib.suppress(Exception):
                await self._redis.publish(_CHANNEL, payload)

    async def send_to_user(self, workspace_id: str, user_id: str, message: dict) -> None:
        """Deliver ``message`` only to ``user_id``'s sockets in the room.

        Used for private pushes (notifications) rather than room-wide fan-out.
        Rides the same Redis channel so a recipient connected to another process
        still receives it, tagged with ``target_user`` so only their sockets get it.
        """
        await self._deliver_local(workspace_id, message, target_user=user_id)
        if self._redis is not None:
            payload = json.dumps(
                {
                    "origin": self._id,
                    "workspace_id": workspace_id,
                    "message": message,
                    "target_user": user_id,
                }
            )
            with contextlib.suppress(Exception):
                await self._redis.publish(_CHANNEL, payload)

    async def _deliver_local(
        self,
        workspace_id: str,
        message: dict,
        *,
        exclude: WebSocket | None = None,
        target_user: str | None = None,
    ) -> None:
        room = self._rooms.get(workspace_id)
        if not room:
            return
        if target_user is not None:
            member = room.get(target_user)
            members = [member] if member is not None else []
        else:
            members = list(room.values())
        targets = [
            conn
            for member in members
            for socket, conn in list(member.connections.items())
            if socket is not exclude
        ]
        for conn in targets:
            try:
                await conn.send(message)
            except Exception:  # noqa: BLE001 - a dead socket must not abort the fan-out
                await self.disconnect(workspace_id, conn.ws, self._owner_of(conn.ws))

    async def _reader(self) -> None:
        """Deliver frames published by *other* processes to our local sockets."""
        assert self._pubsub is not None
        try:
            async for raw in self._pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                except (TypeError, ValueError):
                    continue
                if data.get("origin") == self._id:
                    continue  # our own broadcast, already delivered locally
                await self._deliver_local(
                    data["workspace_id"], data["message"], target_user=data.get("target_user")
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a reader crash must not take down the app
            return

    def _owner_of(self, ws: WebSocket) -> str:
        for room in self._rooms.values():
            for member in room.values():
                if ws in member.connections:
                    return member.user_id
        return ""


manager = ConnectionManager()

