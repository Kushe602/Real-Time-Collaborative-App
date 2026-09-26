"""Shared data-access helpers used by both HTTP routers and the WebSocket layer."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Membership, User, Workspace


async def member_or_none(
    db: AsyncSession, workspace_id: str, user_id: str
) -> Membership | None:
    return await db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )


async def require_workspace(
    db: AsyncSession, workspace_id: str, user: User
) -> Workspace:
    """Return the workspace if ``user`` is a member, else raise 404 (don't leak existence)."""
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    membership = await member_or_none(db, workspace_id, user.id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


async def log_activity(
    db: AsyncSession, workspace_id: str, user_id: str | None, summary: str
) -> Activity:
    activity = Activity(workspace_id=workspace_id, user_id=user_id, summary=summary)
    db.add(activity)
    await db.flush()
    return activity


async def members_of(db: AsyncSession, workspace_id: str) -> list[User]:
    rows = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(User.display_name)
    )
    return list(rows.scalars())
