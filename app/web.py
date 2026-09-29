"""Shared web plumbing: the Jinja2 template environment and static-files mount."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_MENTION_RE = re.compile(r"@([a-z0-9_]{1,40})", re.IGNORECASE)


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _mentions(body: str) -> Markup:
    """Escape a comment body, then highlight ``@name`` tokens as safe markup."""
    esc = str(escape(body or ""))
    return Markup(_MENTION_RE.sub(r'<span class="cs-mention">@\1</span>', esc))


templates.env.filters["initials"] = _initials
templates.env.filters["mentions"] = _mentions
templates.env.globals["app_name"] = "CollabSpace"

static_files = StaticFiles(directory=str(STATIC_DIR))
