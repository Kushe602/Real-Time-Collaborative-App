"""CollabSpace application: lifespan, static files, routers, and auth error handling."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

from app.database import init_db
from app.dependencies import NotAuthenticated
from app.realtime import socket
from app.realtime.manager import manager
from app.routers import auth, surfaces, workspaces
from app.web import static_files


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await manager.startup()
    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(title="CollabSpace", lifespan=lifespan)
app.mount("/static", static_files, name="static")

app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(surfaces.router)
app.include_router(socket.router)


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> Response:
    """Anonymous users hitting a protected route are sent to the login page."""
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = "/login"
        return response
    return RedirectResponse("/login", status_code=303)
