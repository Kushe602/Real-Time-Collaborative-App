"""Authentication routes: register, login, and logout (session-cookie based)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_optional_user
from app.models import User
from app.security import (
    COOKIE_NAME,
    create_access_token,
    hash_password,
    verify_password,
)
from app.services import unique_username
from app.web import templates

router = APIRouter()

# Avatar colours handed out to new users, cycled deterministically-ish.
PALETTE = [
    "#6366f1", "#ec4899", "#f59e0b", "#10b981",
    "#3b82f6", "#ef4444", "#8b5cf6", "#14b8a6",
]


def _set_session_cookie(response, user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(user_id),
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,  # True behind HTTPS in production (COOKIE_SECURE)
        path="/",
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/register.html", {"error": None})


@router.post("/register")
async def register(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    display_name = display_name.strip()
    email = email.strip().lower()
    error = None
    if not display_name:
        error = "Please enter a display name."
    elif "@" not in email or "." not in email:
        error = "Please enter a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    else:
        existing = await db.scalar(select(User).where(User.email == email))
        if existing:
            error = "An account with that email already exists."
    if error:
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": error}, status_code=400
        )

    user = User(
        email=email,
        username=await unique_username(db, email),
        display_name=display_name,
        hashed_password=hash_password(password),
        color=secrets.choice(PALETTE),
    )
    db.add(user)
    await db.commit()

    response = RedirectResponse("/dashboard", status_code=303)
    _set_session_cookie(response, user.id)
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Invalid email or password."},
            status_code=400,
        )
    response = RedirectResponse("/dashboard", status_code=303)
    _set_session_cookie(response, user.id)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response

