"""Workspace routes: landing, dashboard, workspace creation/join, and the shell page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models import (
    Activity,
    Board,
    BoardList,
    Doc,
    Membership,
    User,
    Whiteboard,
    Workspace,
)
from app.services import log_activity, members_of, require_workspace
from app.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "landing.html", {"user": user})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at.desc())
    )
    workspaces = list(rows.scalars())
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "workspaces": workspaces}
    )


@router.post("/workspaces")
async def create_workspace(
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    name = name.strip() or "Untitled workspace"
    workspace = Workspace(name=name, owner_id=user.id)
    db.add(workspace)
    await db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))

    # Seed the workspace so it opens with something to collaborate on.
    board = Board(workspace_id=workspace.id, title="Project board", position=1000.0)
    db.add(board)
    await db.flush()
    for i, title in enumerate(("To do", "In progress", "Done")):
        db.add(BoardList(board_id=board.id, title=title, position=(i + 1) * 1000.0))
    db.add(
        Doc(
            workspace_id=workspace.id,
            title="Welcome",
            content="# Welcome to your workspace\n\nStart typing — everyone sees edits live.",
            position=1000.0,
        )
    )
    db.add(Whiteboard(workspace_id=workspace.id, title="Ideas", position=1000.0))
    await log_activity(db, workspace.id, user.id, f"created the workspace “{name}”")
    await db.commit()
    return RedirectResponse(f"/workspaces/{workspace.id}", status_code=303)


@router.post("/workspaces/join")
async def join_workspace(
    code: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = code.strip()
    workspace = await db.get(Workspace, code)
    if workspace is None:
        return RedirectResponse("/dashboard?error=notfound", status_code=303)
    existing = await db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id, Membership.user_id == user.id
        )
    )
    if existing is None:
        db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="member"))
        await log_activity(db, workspace.id, user.id, "joined the workspace")
        await db.commit()
    return RedirectResponse(f"/workspaces/{workspace.id}", status_code=303)


@router.post("/workspaces/{workspace_id}/surfaces")
async def create_surface(
    workspace_id: str,
    kind: str = Form(...),
    title: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_workspace(db, workspace_id, user)
    title = title.strip()
    if kind == "board":
        db.add(Board(workspace_id=workspace_id, title=title or "Untitled board"))
        label = title or "Untitled board"
    elif kind == "doc":
        db.add(Doc(workspace_id=workspace_id, title=title or "Untitled doc"))
        label = title or "Untitled doc"
    elif kind == "whiteboard":
        db.add(Whiteboard(workspace_id=workspace_id, title=title or "Untitled whiteboard"))
        label = title or "Untitled whiteboard"
    else:
        return RedirectResponse(f"/workspaces/{workspace_id}", status_code=303)
    await log_activity(db, workspace_id, user.id, f"added the {kind} “{label}”")
    await db.commit()
    return RedirectResponse(f"/workspaces/{workspace_id}", status_code=303)


@router.get("/workspaces/{workspace_id}", response_class=HTMLResponse)
async def open_workspace(
    workspace_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace = await require_workspace(db, workspace_id, user)

    boards = list(
        await db.scalars(
            select(Board).where(Board.workspace_id == workspace_id).order_by(Board.position)
        )
    )
    docs = list(
        await db.scalars(
            select(Doc).where(Doc.workspace_id == workspace_id).order_by(Doc.position)
        )
    )
    whiteboards = list(
        await db.scalars(
            select(Whiteboard)
            .where(Whiteboard.workspace_id == workspace_id)
            .order_by(Whiteboard.position)
        )
    )
    activities = list(
        await db.scalars(
            select(Activity)
            .where(Activity.workspace_id == workspace_id)
            .order_by(Activity.created_at.desc())
            .limit(30)
        )
    )
    members = await members_of(db, workspace_id)

    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "user": user,
            "workspace": workspace,
            "boards": boards,
            "docs": docs,
            "whiteboards": whiteboards,
            "activities": activities,
            "members": members,
            "member_names": {m.id: m.display_name for m in members},
        },
    )

