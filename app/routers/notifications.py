"""Notification routes: mark a single in-app notification (or all of them) read.

Notifications are created and pushed live by the WebSocket layer; these HTTP
endpoints back the feed's "mark read" affordances so the badge state persists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Notification, User
from app.services import require_workspace

router = APIRouter()


@router.post("/workspaces/{workspace_id}/notifications/{note_id}/read")
async def mark_read(
    workspace_id: str,
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark one of the current user's notifications read (no-op if it isn't theirs)."""
    await require_workspace(db, workspace_id, user)
    note = await db.get(Notification, note_id)
    if note is not None and note.workspace_id == workspace_id and note.user_id == user.id:
        note.read = True
        await db.commit()
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/notifications/read-all")
async def mark_all_read(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear the current user's unread notifications for the workspace."""
    await require_workspace(db, workspace_id, user)
    rows = await db.scalars(
        select(Notification).where(
            Notification.workspace_id == workspace_id,
            Notification.user_id == user.id,
            Notification.read.is_(False),
        )
    )
    for note in rows:
        note.read = True
    await db.commit()
    return Response(status_code=204)
