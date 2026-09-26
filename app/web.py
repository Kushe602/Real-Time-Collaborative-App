"""Shared web plumbing: the Jinja2 template environment and static-files mount."""
from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


templates.env.filters["initials"] = _initials
templates.env.globals["app_name"] = "CollabSpace"

static_files = StaticFiles(directory=str(STATIC_DIR))
