"""Channel handlers for the workspace WebSocket.

Each handler receives ``(workspace_id, user, mtype, message, ws)`` and is
responsible for persisting any change and broadcasting the result to the room.
Keeping them here keeps :mod:`app.realtime.socket` a thin transport loop.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import WebSocket

from app.database import SessionLocal
from app.models import Board, BoardList, Card, ChatMessage, Doc, User, Whiteboard, WhiteboardElement
from app.realtime.manager import manager
from app.web import templates


def _render(template_name: str, context: dict) -> str:
    """Render a Jinja partial to an HTML string (no Request needed) for broadcasting."""
    return templates.env.get_template(template_name).render(**context)


async def dispatch(workspace_id: str, user: User, message: dict, ws: WebSocket) -> None:
    channel = message.get("channel")
    mtype = message.get("type")
    handler = _ROUTES.get(channel)
    if handler is not None:
        await handler(workspace_id, user, mtype, message, ws)


async def _presence(workspace_id, user, mtype, msg, ws):
    if mtype == "cursor":
        cursor = msg.get("cursor")
        surface = msg.get("surface")
        manager.set_cursor(workspace_id, user.id, cursor, surface)
        await manager.broadcast(
            workspace_id,
            {
                "channel": "presence",
                "type": "cursor",
                "user_id": user.id,
                "display_name": user.display_name,
                "color": user.color,
                "cursor": cursor,
                "surface": surface,
            },
            exclude=ws,
        )


async def _chat(workspace_id, user, mtype, msg, ws):
    if mtype != "send":
        return
    body = (msg.get("body") or "").strip()[:2000]
    if not body:
        return
    async with SessionLocal() as db:
        row = ChatMessage(workspace_id=workspace_id, user_id=user.id, body=body)
        db.add(row)
        await db.commit()
        message_id, created = row.id, row.created_at
    await manager.broadcast(
        workspace_id,
        {
            "channel": "chat",
            "type": "message",
            "id": message_id,
            "user_id": user.id,
            "display_name": user.display_name,
            "color": user.color,
            "body": body,
            "at": (created or datetime.now(UTC)).isoformat(),
        },
    )


async def _board(workspace_id, user, mtype, msg, ws):
    if mtype == "card.create":
        list_id = msg.get("list_id")
        title = (msg.get("title") or "").strip()[:500]
        position = float(msg.get("position") or 1000.0)
        if not list_id or not title:
            return
        async with SessionLocal() as db:
            lst = await db.get(BoardList, list_id)
            if lst is None:
                return
            board = await db.get(Board, lst.board_id)
            if board is None or board.workspace_id != workspace_id:
                return
            card = Card(list_id=list_id, title=title, position=position)
            db.add(card)
            await db.commit()
            html = _render("partials/card.html", {"card": card})
        await manager.broadcast(
            workspace_id,
            {
                "channel": "board",
                "type": "card.created",
                "board_id": board.id,
                "list_id": list_id,
                "position": position,
                "html": html,
            },
        )
    elif mtype == "card.move":
        card_id = msg.get("card_id")
        list_id = msg.get("list_id")
        position = float(msg.get("position") or 1000.0)
        async with SessionLocal() as db:
            card = await db.get(Card, card_id)
            lst = await db.get(BoardList, list_id) if list_id else None
            if card is None or lst is None:
                return
            board = await db.get(Board, lst.board_id)
            if board is None or board.workspace_id != workspace_id:
                return
            card.list_id = list_id
            card.position = position
            await db.commit()
        await manager.broadcast(
            workspace_id,
            {
                "channel": "board",
                "type": "card.moved",
                "board_id": board.id,
                "card_id": card_id,
                "list_id": list_id,
                "position": position,
            },
            exclude=ws,
        )
    elif mtype == "list.create":
        await _create_list(workspace_id, msg)


async def _doc(workspace_id, user, mtype, msg, ws):
    """Collaborative doc edits: last-write-wins, server owns the version counter."""
    if mtype != "doc.update":
        return
    doc_id = msg.get("doc_id")
    if not doc_id:
        return
    content = (msg.get("content") or "")[:100_000]
    async with SessionLocal() as db:
        doc = await db.get(Doc, doc_id)
        if doc is None or doc.workspace_id != workspace_id:
            return
        doc.content = content
        doc.version += 1
        doc.updated_at = datetime.now(UTC)
        await db.commit()
        version = doc.version
    await manager.broadcast(
        workspace_id,
        {
            "channel": "doc",
            "type": "doc.updated",
            "doc_id": doc_id,
            "content": content,
            "version": version,
            "user_id": user.id,
            "display_name": user.display_name,
            "color": user.color,
        },
        exclude=ws,
    )


# <APPEND-HANDLERS>
async def _create_list(workspace_id, msg):
    board_id = msg.get("board_id")
    title = (msg.get("title") or "").strip()[:120]
    position = float(msg.get("position") or 1000.0)
    if not board_id or not title:
        return
    async with SessionLocal() as db:
        board = await db.get(Board, board_id)
        if board is None or board.workspace_id != workspace_id:
            return
        lst = BoardList(board_id=board_id, title=title, position=position)
        db.add(lst)
        await db.commit()
        html = _render("partials/board_list.html", {"lst": lst, "cards": []})
    await manager.broadcast(
        workspace_id,
        {"channel": "board", "type": "list.created", "board_id": board_id, "html": html},
    )


_ELEMENT_KINDS = {"note", "rect", "ellipse"}


def _clean_element_data(data: dict) -> dict:
    """Whitelist and coerce whiteboard element fields coming off the wire."""
    out: dict = {}
    for key in ("x", "y", "w", "h"):
        if key in data:
            try:
                out[key] = float(data[key])
            except (TypeError, ValueError):
                pass
    color = data.get("color")
    if isinstance(color, str):
        out["color"] = color[:9]
    if data.get("text") is not None:
        out["text"] = str(data["text"])[:2000]
    return out


async def _whiteboard(workspace_id, user, mtype, msg, ws):
    """Sticky notes and shapes: create broadcasts to all, move/update exclude the sender."""
    wb_id = msg.get("whiteboard_id")
    if not wb_id:
        return
    if mtype == "element.create":
        kind = msg.get("kind")
        if kind not in _ELEMENT_KINDS:
            return
        data = _clean_element_data(msg.get("data") or {})
        async with SessionLocal() as db:
            wb = await db.get(Whiteboard, wb_id)
            if wb is None or wb.workspace_id != workspace_id:
                return
            el = WhiteboardElement(whiteboard_id=wb_id, kind=kind, data=data)
            db.add(el)
            await db.commit()
            el_id = el.id
        await manager.broadcast(
            workspace_id,
            {
                "channel": "whiteboard", "type": "element.created", "whiteboard_id": wb_id,
                "id": el_id, "kind": kind, "data": data,
            },
        )
    elif mtype == "element.move":
        el_id = msg.get("element_id")
        try:
            x, y = float(msg.get("x")), float(msg.get("y"))
        except (TypeError, ValueError):
            return
        async with SessionLocal() as db:
            el = await db.get(WhiteboardElement, el_id) if el_id else None
            if el is None:
                return
            wb = await db.get(Whiteboard, el.whiteboard_id)
            if wb is None or wb.id != wb_id or wb.workspace_id != workspace_id:
                return
            el.data = {**el.data, "x": x, "y": y}
            await db.commit()
        await manager.broadcast(
            workspace_id,
            {
                "channel": "whiteboard", "type": "element.moved", "whiteboard_id": wb_id,
                "element_id": el_id, "x": x, "y": y,
            },
            exclude=ws,
        )
    elif mtype == "element.update":
        el_id = msg.get("element_id")
        patch = _clean_element_data(msg.get("data") or {})
        async with SessionLocal() as db:
            el = await db.get(WhiteboardElement, el_id) if el_id else None
            if el is None:
                return
            wb = await db.get(Whiteboard, el.whiteboard_id)
            if wb is None or wb.id != wb_id or wb.workspace_id != workspace_id:
                return
            el.data = {**el.data, **patch}
            await db.commit()
            new_data = el.data
        await manager.broadcast(
            workspace_id,
            {
                "channel": "whiteboard", "type": "element.updated", "whiteboard_id": wb_id,
                "element_id": el_id, "data": new_data,
            },
            exclude=ws,
        )


_ROUTES = {
    "presence": _presence,
    "chat": _chat,
    "board": _board,
    "doc": _doc,
    "whiteboard": _whiteboard,
}
