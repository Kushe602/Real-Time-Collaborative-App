"""Surface routes: render a board/doc/whiteboard as an HTML partial into the shell.

The workspace shell fetches these over HTTP when a user opens a surface; all
subsequent live edits flow over the workspace WebSocket.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Board, BoardList, Card, Doc, User, Whiteboard, WhiteboardElement
from app.services import require_workspace
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
    return templates.TemplateResponse(
        request,
        "partials/board.html",
        {"board": board, "lists": lists, "cards_by_list": cards_by_list},
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

