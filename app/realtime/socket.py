"""The single WebSocket per user per workspace that multiplexes every live channel.

Client → server and server → client messages share one envelope::

    {"channel": "board|doc|whiteboard|chat|presence", "type": "...", ...payload}

Structural reads (opening a surface) happen over HTTP; every *live mutation*
(card moves, doc edits, whiteboard drags, chat, cursors) travels over this socket.
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.realtime import handlers
from app.realtime.manager import manager
from app.security import COOKIE_NAME, decode_token
from app.services import member_or_none

router = APIRouter()


async def _authenticate(ws: WebSocket, workspace_id: str) -> User | None:
    """Resolve the user from the session cookie and confirm workspace membership."""
    token = ws.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = decode_token(token)
    if not user_id:
        return None
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        if await member_or_none(db, workspace_id, user.id) is None:
            return None
        return user


async def _heartbeat(ws: WebSocket) -> None:
    """Keep the connection alive through idle proxies with app-level pings."""
    try:
        while True:
            await asyncio.sleep(settings.ws_heartbeat_seconds)
            await ws.send_json({"channel": "system", "type": "ping"})
    except Exception:  # noqa: BLE001 - socket closing races the sleep; just stop
        return


@router.websocket("/ws/workspace/{workspace_id}")
async def workspace_ws(ws: WebSocket, workspace_id: str):
    user = await _authenticate(ws, workspace_id)
    if user is None:
        # Accept-then-close so the browser sees a clean close rather than a handshake error.
        await ws.accept()
        await ws.close(code=4401)
        return

    await manager.connect(workspace_id, ws, user)
    me = {"user_id": user.id, "display_name": user.display_name, "color": user.color}
    await ws.send_json(
        {"channel": "presence", "type": "roster", "members": manager.roster(workspace_id), "me": me}
    )
    await manager.broadcast(
        workspace_id, {"channel": "presence", "type": "join", "member": me}, exclude=ws
    )

    heartbeat = asyncio.create_task(_heartbeat(ws))
    try:
        while True:
            message = await ws.receive_json()
            await handlers.dispatch(workspace_id, user, message, ws)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never let a bad frame crash the socket task
        pass
    finally:
        heartbeat.cancel()
        with contextlib.suppress(BaseException):
            await heartbeat
        fully_left = await manager.disconnect(workspace_id, ws, user.id)
        if fully_left:
            await manager.broadcast(
                workspace_id, {"channel": "presence", "type": "leave", "user_id": user.id}
            )

