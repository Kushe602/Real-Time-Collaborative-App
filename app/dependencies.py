"""FastAPI dependencies: current-user resolution from the session cookie."""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.security import COOKIE_NAME, decode_token


class NotAuthenticated(Exception):
    """Raised by :func:`get_current_user` when no valid session cookie is present.

    A global handler (see ``app.main``) turns this into a redirect to ``/login``
    for full-page requests, or an ``HX-Redirect`` header for HTMX requests.
    """


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the signed-in user, or ``None`` for anonymous visitors."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = decode_token(token)
    if not user_id:
        return None
    return await db.get(User, user_id)


async def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    """Require an authenticated user, else raise :class:`NotAuthenticated`."""
    if user is None:
        raise NotAuthenticated
    return user
