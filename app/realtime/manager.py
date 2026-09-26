"""In-process room registry for workspace WebSocket connections.

Each workspace is a *room*; every open socket in a room receives broadcasts.
Presence tracks who is connected (deduplicated by user, since one user may have
several tabs open) plus their last-known cursor and the surface they are viewing.

This registry is intentionally in-memory and single-process. To scale out you
would put a Redis (or similar) pub/sub fan-out behind this same interface; the
rest of the app talks only to :class:`ConnectionManager`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket


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

    async def disconnect(self, workspace_id: str, ws: WebSocket, user_id: str) -> bool:
        """Drop one socket. Return ``True`` if the user has now fully left the room."""
        async with self._lock:
            room = self._rooms.get(workspace_id)
            if not room or user_id not in room:
                return False
            member = room[user_id]
            member.connections.pop(ws, None)
            if member.connections:
                return False
            del room[user_id]
            if not room:
                self._rooms.pop(workspace_id, None)
            return True

    def roster(self, workspace_id: str) -> list[dict]:
        """The public presence list for a room (one entry per connected user)."""
        room = self._rooms.get(workspace_id, {})
        return [m.public() for m in room.values()]

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
        """Send ``message`` to every socket in the room except ``exclude``."""
        room = self._rooms.get(workspace_id)
        if not room:
            return
        targets = [
            conn
            for member in list(room.values())
            for socket, conn in list(member.connections.items())
            if socket is not exclude
        ]
        for conn in targets:
            try:
                await conn.send(message)
            except Exception:  # noqa: BLE001 - a dead socket must not abort the fan-out
                await self.disconnect(workspace_id, conn.ws, self._owner_of(conn.ws))

    def _owner_of(self, ws: WebSocket) -> str:
        for room in self._rooms.values():
            for member in room.values():
                if ws in member.connections:
                    return member.user_id
        return ""


manager = ConnectionManager()

