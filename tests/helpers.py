"""Small helpers shared across the test modules.

These wrap the HTTP form endpoints the browser uses, so tests can arrange state
(a registered user, a workspace) without repeating the request boilerplate.
"""
from __future__ import annotations

import re


def register(
    client,
    *,
    display_name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    password: str = "supersecret1",
    follow_redirects: bool = False,
):
    """Register a user; the auth cookie is stored on the client's jar."""
    return client.post(
        "/register",
        data={"display_name": display_name, "email": email, "password": password},
        follow_redirects=follow_redirects,
    )


def login(client, *, email: str, password: str, follow_redirects: bool = False):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


def make_workspace(client, name: str = "Test Workspace") -> str:
    """Create a workspace and return its id (parsed from the redirect target)."""
    r = client.post("/workspaces", data={"name": name}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[-1]


def surface_ids(html: str, kind: str) -> list[str]:
    """Pull surface ids of a given kind (board/doc/whiteboard) out of the shell."""
    return re.findall(rf'data-surface="{kind}:([0-9a-fA-F]+)"', html)


def list_ids(html: str) -> list[str]:
    """Pull board-list ids out of a rendered board partial."""
    return re.findall(r'data-list="([0-9a-fA-F]+)"', html)
