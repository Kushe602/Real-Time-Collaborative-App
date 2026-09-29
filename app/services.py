"""Shared data-access helpers used by both HTTP routers and the WebSocket layer."""
from __future__ import annotations

import re
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, DocVersion, Invite, Membership, Notification, User, Workspace

# How long a freshly minted invite code stays valid.
INVITE_TTL_SECONDS = 7 * 86400

# An @mention token: "@" followed by a username-shaped run of characters.
MENTION_RE = re.compile(r"@([a-z0-9_]{1,40})", re.IGNORECASE)


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


async def member_map(db: AsyncSession, workspace_id: str) -> dict[str, User]:
    """``{user_id: User}`` for every member — handy for resolving assignees/authors."""
    return {u.id: u for u in await members_of(db, workspace_id)}


async def unique_username(db: AsyncSession, email: str) -> str:
    """Derive a stable, unique @handle from an email's local part.

    ``ada@example.com`` → ``ada``; collisions get a numeric suffix (``ada2`` …).
    """
    base = re.sub(r"[^a-z0-9_]", "", email.split("@", 1)[0].lower()) or "user"
    candidate = base
    n = 1
    while await db.scalar(select(User).where(User.username == candidate)) is not None:
        n += 1
        candidate = f"{base}{n}"
    return candidate


def parse_mentions(body: str) -> set[str]:
    """Return the lowercased set of usernames referenced as ``@name`` in ``body``."""
    return {m.lower() for m in MENTION_RE.findall(body or "")}


async def create_notification(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
    actor_id: str | None,
    kind: str,
    body: str,
    board_id: str | None = None,
    card_id: str | None = None,
) -> Notification:
    """Persist a notification for ``user_id`` (flushed, not committed)."""
    note = Notification(
        workspace_id=workspace_id,
        user_id=user_id,
        actor_id=actor_id,
        kind=kind,
        body=body,
        board_id=board_id,
        card_id=card_id,
    )
    db.add(note)
    await db.flush()
    return note


async def snapshot_doc_version(
    db: AsyncSession, *, doc_id: str, version: int, content: str, author_id: str | None
) -> DocVersion:
    """Append a doc snapshot for the given version number (flushed, not committed)."""
    snap = DocVersion(doc_id=doc_id, version=version, content=content, author_id=author_id)
    db.add(snap)
    await db.flush()
    return snap


async def create_invite(db: AsyncSession, workspace_id: str, user_id: str) -> Invite:
    """Mint a fresh invite code that expires in :data:`INVITE_TTL_SECONDS`."""
    invite = Invite(
        workspace_id=workspace_id,
        created_by=user_id,
        expires_at=time.time() + INVITE_TTL_SECONDS,
    )
    db.add(invite)
    await db.flush()
    return invite


async def active_invite(db: AsyncSession, workspace_id: str) -> Invite | None:
    """The newest still-valid invite for a workspace, or ``None`` if none is live.

    Rotation always creates a newer row, so the most recent invite is the freshest
    one; if it is revoked or past its expiry, there is no active invite.
    """
    invite = await db.scalar(
        select(Invite)
        .where(Invite.workspace_id == workspace_id)
        .order_by(Invite.created_at.desc())
    )
    if invite is None or invite.revoked or invite.expires_at <= time.time():
        return None
    return invite


async def rotate_invite(db: AsyncSession, workspace_id: str, user_id: str) -> Invite:
    """Revoke every existing invite for the workspace and mint a new one."""
    existing = await db.scalars(select(Invite).where(Invite.workspace_id == workspace_id))
    for invite in existing:
        invite.revoked = True
    return await create_invite(db, workspace_id, user_id)


async def resolve_invite(db: AsyncSession, code: str) -> Invite | None:
    """Return the invite for ``code`` only if it is neither revoked nor expired."""
    invite = await db.scalar(select(Invite).where(Invite.code == code))
    if invite is None or invite.revoked or invite.expires_at <= time.time():
        return None
    return invite
