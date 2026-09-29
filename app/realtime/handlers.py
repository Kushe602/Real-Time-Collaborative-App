"""Channel handlers for the workspace WebSocket.

Each handler receives ``(workspace_id, user, mtype, message, ws)`` and is
responsible for persisting any change and broadcasting the result to the room.
Keeping them here keeps :mod:`app.realtime.socket` a thin transport loop.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    Board,
    BoardList,
    Card,
    CardComment,
    ChatMessage,
    Doc,
    DocVersion,
    User,
    Whiteboard,
    WhiteboardElement,
)
from app.realtime.manager import manager
from app.services import (
    create_notification,
    member_map,
    parse_mentions,
    snapshot_doc_version,
)
from app.web import templates


def _render(template_name: str, context: dict) -> str:
    """Render a Jinja partial to an HTML string (no Request needed) for broadcasting."""
    return templates.env.get_template(template_name).render(**context)


def _short(text: str, limit: int = 60) -> str:
    text = (text or "").strip() or "a card"
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _comment_count(db, card_id: str) -> int:
    return await db.scalar(
        select(func.count()).select_from(CardComment).where(CardComment.card_id == card_id)
    ) or 0


def _card_public(card: Card, members: dict[str, User]) -> dict:
    """Flatten a card (plus its resolved assignee) for open detail panels."""
    assignee = members.get(card.assignee_id) if card.assignee_id else None
    return {
        "id": card.id,
        "list_id": card.list_id,
        "title": card.title,
        "description": card.description or "",
        "position": card.position,
        "assignee_id": card.assignee_id,
        "assignee_name": assignee.display_name if assignee else None,
        "assignee_color": assignee.color if assignee else None,
        "due_date": card.due_date,
        "labels": card.labels or [],
        "checklist": card.checklist or [],
    }


def _render_notification(note, actor: User | None) -> dict:
    html = _render("partials/notification.html", {"n": note, "actor": actor})
    return {
        "channel": "notifications",
        "type": "notification.new",
        "id": note.id,
        "html": html,
        "kind": note.kind,
        "board_id": note.board_id,
        "card_id": note.card_id,
    }


_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_due_date(value) -> str | None:
    if isinstance(value, str) and _DUE_RE.match(value.strip()):
        return value.strip()
    return None


def _clean_labels(value) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()[:40]
        color = item.get("color")
        color = color[:9] if isinstance(color, str) else "#6366f1"
        if text:
            out.append({"text": text, "color": color})
    return out


def _clean_checklist(value) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()[:200]
        if not text:
            continue
        item_id = str(item.get("id") or uuid4().hex)[:32]
        out.append({"id": item_id, "text": text, "done": bool(item.get("done"))})
    return out


def _apply_card_patch(card: Card, patch: dict, members: dict[str, User]) -> None:
    """Merge a whitelisted patch of editable fields onto ``card`` in place."""
    if "title" in patch:
        title = str(patch["title"]).strip()[:500]
        if title:
            card.title = title
    if "description" in patch:
        card.description = str(patch["description"])[:20_000]
    if "assignee_id" in patch:
        aid = patch["assignee_id"]
        if aid in (None, "", "null"):
            card.assignee_id = None
        elif aid in members:
            card.assignee_id = aid
    if "due_date" in patch:
        card.due_date = _clean_due_date(patch["due_date"])
    if "labels" in patch:
        card.labels = _clean_labels(patch["labels"])
    if "checklist" in patch:
        card.checklist = _clean_checklist(patch["checklist"])


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
            html = _render(
                "partials/card.html", {"card": card, "members": {}, "comment_count": 0}
            )
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
    elif mtype == "card.update":
        await _card_update(workspace_id, user, msg)
    elif mtype == "comment.add":
        await _comment_add(workspace_id, user, msg)
    elif mtype == "list.create":
        await _create_list(workspace_id, msg)


async def _card_update(workspace_id, user, msg):
    """Apply an edit to a card's rich fields and re-broadcast the rendered card."""
    card_id = msg.get("card_id")
    patch = msg.get("patch")
    if not card_id or not isinstance(patch, dict):
        return
    notif = None
    async with SessionLocal() as db:
        card = await db.get(Card, card_id)
        if card is None:
            return
        lst = await db.get(BoardList, card.list_id)
        board = await db.get(Board, lst.board_id) if lst else None
        if board is None or board.workspace_id != workspace_id:
            return
        members = await member_map(db, workspace_id)
        prev_assignee = card.assignee_id
        _apply_card_patch(card, patch, members)
        assignee = card.assignee_id
        note = None
        if assignee and assignee != prev_assignee and assignee != user.id:
            note = await create_notification(
                db,
                workspace_id=workspace_id,
                user_id=assignee,
                actor_id=user.id,
                kind="assign",
                body=f"assigned you to “{_short(card.title)}”",
                board_id=board.id,
                card_id=card.id,
            )
        await db.commit()
        count = await _comment_count(db, card.id)
        html = _render(
            "partials/card.html", {"card": card, "members": members, "comment_count": count}
        )
        pub = _card_public(card, members)
        if note is not None:
            notif = (assignee, _render_notification(note, user))
    await manager.broadcast(
        workspace_id,
        {
            "channel": "board",
            "type": "card.updated",
            "board_id": board.id,
            "card_id": card.id,
            "list_id": card.list_id,
            "html": html,
            "card": pub,
        },
    )
    if notif is not None:
        await manager.send_to_user(workspace_id, notif[0], notif[1])


async def _comment_add(workspace_id, user, msg):
    """Persist a card comment, notify @mentioned members, and stream it live."""
    card_id = msg.get("card_id")
    body = (msg.get("body") or "").strip()[:2000]
    if not card_id or not body:
        return
    notif_msgs: list[tuple[str, dict]] = []
    async with SessionLocal() as db:
        card = await db.get(Card, card_id)
        if card is None:
            return
        lst = await db.get(BoardList, card.list_id)
        board = await db.get(Board, lst.board_id) if lst else None
        if board is None or board.workspace_id != workspace_id:
            return
        members = await member_map(db, workspace_id)
        comment = CardComment(card_id=card_id, user_id=user.id, body=body)
        db.add(comment)
        await db.flush()
        by_username = {u.username.lower(): u for u in members.values()}
        notes = []
        for uname in parse_mentions(body):
            target = by_username.get(uname)
            if target is not None and target.id != user.id:
                note = await create_notification(
                    db,
                    workspace_id=workspace_id,
                    user_id=target.id,
                    actor_id=user.id,
                    kind="mention",
                    body=f"mentioned you in a comment on “{_short(card.title)}”",
                    board_id=board.id,
                    card_id=card.id,
                )
                notes.append(note)
        await db.commit()
        count = await _comment_count(db, card.id)
        comment_html = _render("partials/comment.html", {"c": comment, "author": user})
        card_html = _render(
            "partials/card.html", {"card": card, "members": members, "comment_count": count}
        )
        comment_id = comment.id
        pub = _card_public(card, members)
        notif_msgs = [(n.user_id, _render_notification(n, user)) for n in notes]
    await manager.broadcast(
        workspace_id,
        {
            "channel": "board",
            "type": "comment.added",
            "board_id": board.id,
            "card_id": card_id,
            "id": comment_id,
            "html": comment_html,
            "comment_count": count,
        },
    )
    await manager.broadcast(
        workspace_id,
        {
            "channel": "board",
            "type": "card.updated",
            "board_id": board.id,
            "card_id": card_id,
            "list_id": card.list_id,
            "html": card_html,
            "card": pub,
        },
    )
    for target_id, m in notif_msgs:
        await manager.send_to_user(workspace_id, target_id, m)


async def _doc(workspace_id, user, mtype, msg, ws):
    """Collaborative doc edits: last-write-wins, server owns the version counter."""
    doc_id = msg.get("doc_id")
    if not doc_id:
        return
    if mtype == "doc.update":
        content = (msg.get("content") or "")[:100_000]
        async with SessionLocal() as db:
            doc = await db.get(Doc, doc_id)
            if doc is None or doc.workspace_id != workspace_id:
                return
            doc.content = content
            doc.version += 1
            doc.updated_at = datetime.now(UTC)
            await snapshot_doc_version(
                db, doc_id=doc.id, version=doc.version, content=content, author_id=user.id
            )
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
    elif mtype == "doc.restore":
        try:
            target = int(msg.get("version"))
        except (TypeError, ValueError):
            return
        async with SessionLocal() as db:
            doc = await db.get(Doc, doc_id)
            if doc is None or doc.workspace_id != workspace_id:
                return
            snap = await db.scalar(
                select(DocVersion).where(
                    DocVersion.doc_id == doc_id, DocVersion.version == target
                )
            )
            if snap is None:
                return
            doc.content = snap.content
            doc.version += 1
            doc.updated_at = datetime.now(UTC)
            await snapshot_doc_version(
                db, doc_id=doc.id, version=doc.version, content=snap.content, author_id=user.id
            )
            await db.commit()
            content, version = snap.content, doc.version
        # Everyone (including the initiator) converges on the restored content.
        await manager.broadcast(
            workspace_id,
            {
                "channel": "doc",
                "type": "doc.updated",
                "doc_id": doc_id,
                "content": content,
                "version": version,
                "restored_from": target,
                "user_id": user.id,
                "display_name": user.display_name,
                "color": user.color,
            },
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


_ELEMENT_KINDS = {"note", "rect", "ellipse", "path", "text"}


def _clean_element_data(data: dict) -> dict:
    """Whitelist and coerce whiteboard element fields coming off the wire."""
    out: dict = {}
    for key in ("x", "y", "w", "h", "size"):
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
    points = data.get("points")
    if isinstance(points, list):
        clean: list[list[float]] = []
        for pt in points[:5000]:
            if isinstance(pt, (list, tuple)) and len(pt) == 2:
                try:
                    clean.append([float(pt[0]), float(pt[1])])
                except (TypeError, ValueError):
                    pass
        out["points"] = clean
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
    elif mtype == "element.delete":
        el_id = msg.get("element_id")
        async with SessionLocal() as db:
            el = await db.get(WhiteboardElement, el_id) if el_id else None
            if el is None:
                return
            wb = await db.get(Whiteboard, el.whiteboard_id)
            if wb is None or wb.id != wb_id or wb.workspace_id != workspace_id:
                return
            await db.delete(el)
            await db.commit()
        await manager.broadcast(
            workspace_id,
            {
                "channel": "whiteboard", "type": "element.deleted", "whiteboard_id": wb_id,
                "element_id": el_id,
            },
        )


_ROUTES = {
    "presence": _presence,
    "chat": _chat,
    "board": _board,
    "doc": _doc,
    "whiteboard": _whiteboard,
}
