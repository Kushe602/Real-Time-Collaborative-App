"""Surface routes: render a board/doc/whiteboard as an HTML partial into the shell.

The workspace shell fetches these over HTTP when a user opens a surface; all
subsequent live edits flow over the workspace WebSocket.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Board,
    BoardList,
    Card,
    CardComment,
    Doc,
    DocVersion,
    User,
    Whiteboard,
    WhiteboardElement,
)
from app.services import member_map, members_of, require_workspace
from app.web import templates

router = APIRouter()


@router.get("/workspaces/{workspace_id}/board/{board_id}", response_class=HTMLResponse)
async def board_partial(
    workspace_id: str,
    board_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_workspace(db, workspace_id, user)
    board = await db.get(Board, board_id)
    if board is None or board.workspace_id != workspace_id:
        return HTMLResponse("Board not found", status_code=404)
    lists = list(
        await db.scalars(
            select(BoardList).where(BoardList.board_id == board_id).order_by(BoardList.position)
        )
    )
    cards = list(
        await db.scalars(
            select(Card)
            .where(Card.list_id.in_([lst.id for lst in lists] or [""]))
            .order_by(Card.position)
        )
    )
    cards_by_list: dict[str, list[Card]] = {lst.id: [] for lst in lists}
    for card in cards:
        cards_by_list.setdefault(card.list_id, []).append(card)
    counts = await db.execute(
        select(CardComment.card_id, func.count())
        .where(CardComment.card_id.in_([c.id for c in cards] or [""]))
        .group_by(CardComment.card_id)
    )
    comment_counts = {card_id: n for card_id, n in counts.all()}
    members = await member_map(db, workspace_id)
    return templates.TemplateResponse(
        request,
        "partials/board.html",
        {
            "board": board,
            "lists": lists,
            "cards_by_list": cards_by_list,
            "members": members,
            "comment_counts": comment_counts,
        },
    )


@router.get("/workspaces/{workspace_id}/card/{card_id}", response_class=HTMLResponse)
async def card_partial(
    workspace_id: str,
    card_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The rich card-detail panel: fields, checklist, and the comment thread."""
    await require_workspace(db, workspace_id, user)
    card = await db.get(Card, card_id)
    lst = await db.get(BoardList, card.list_id) if card else None
    board = await db.get(Board, lst.board_id) if lst else None
    if board is None or board.workspace_id != workspace_id:
        return HTMLResponse("Card not found", status_code=404)
    members = await members_of(db, workspace_id)
    comments = list(
        await db.scalars(
            select(CardComment)
            .where(CardComment.card_id == card_id)
            .order_by(CardComment.created_at)
        )
    )
    return templates.TemplateResponse(
        request,
        "partials/card_detail.html",
        {
            "card": card,
            "members": members,
            "comments": comments,
            "authors": {m.id: m for m in members},
            "board_id": board.id,
        },
    )


@router.get("/workspaces/{workspace_id}/doc/{doc_id}", response_class=HTMLResponse)
async def doc_partial(
    workspace_id: str,
    doc_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_workspace(db, workspace_id, user)
    doc = await db.get(Doc, doc_id)
    if doc is None or doc.workspace_id != workspace_id:
        return HTMLResponse("Doc not found", status_code=404)
    return templates.TemplateResponse(request, "partials/doc.html", {"doc": doc})


@router.get("/workspaces/{workspace_id}/doc/{doc_id}/versions", response_class=HTMLResponse)
async def doc_versions_partial(
    workspace_id: str,
    doc_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The saved-version timeline for a doc, newest first, with restore controls."""
    await require_workspace(db, workspace_id, user)
    doc = await db.get(Doc, doc_id)
    if doc is None or doc.workspace_id != workspace_id:
        return HTMLResponse("Doc not found", status_code=404)
    versions = list(
        await db.scalars(
            select(DocVersion)
            .where(DocVersion.doc_id == doc_id)
            .order_by(DocVersion.version.desc())
        )
    )
    author_ids = {v.author_id for v in versions if v.author_id}
    authors: dict[str, User] = {}
    if author_ids:
        rows = await db.scalars(select(User).where(User.id.in_(author_ids)))
        authors = {u.id: u for u in rows}
    return templates.TemplateResponse(
        request,
        "partials/doc_versions.html",
        {"doc": doc, "versions": versions, "authors": authors},
    )


@router.get("/workspaces/{workspace_id}/whiteboard/{whiteboard_id}", response_class=HTMLResponse)
async def whiteboard_partial(
    workspace_id: str,
    whiteboard_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_workspace(db, workspace_id, user)
    wb = await db.get(Whiteboard, whiteboard_id)
    if wb is None or wb.workspace_id != workspace_id:
        return HTMLResponse("Whiteboard not found", status_code=404)
    elements = list(
        await db.scalars(
            select(WhiteboardElement)
            .where(WhiteboardElement.whiteboard_id == whiteboard_id)
            .order_by(WhiteboardElement.created_at)
        )
    )
    payload = [{"id": e.id, "kind": e.kind, "data": e.data} for e in elements]
    return templates.TemplateResponse(
        request, "partials/whiteboard.html", {"wb": wb, "elements": payload}
    )

