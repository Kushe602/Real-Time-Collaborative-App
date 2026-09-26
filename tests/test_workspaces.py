"""Workspace lifecycle: creation (with seeded surfaces), membership gating, and join."""
from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app
from tests.helpers import make_workspace, register, surface_ids


def test_create_workspace_seeds_surfaces(auth_client):
    ws_id = make_workspace(auth_client, "Launch Team")
    body = auth_client.get(f"/workspaces/{ws_id}").text

    # The three seeded surfaces show up by title in the sidebar…
    assert "Project board" in body
    assert "Welcome" in body
    assert "Ideas" in body
    # …and each surface kind has a clickable entry.
    assert surface_ids(body, "board")
    assert surface_ids(body, "doc")
    assert surface_ids(body, "whiteboard")


def test_open_workspace_404_for_non_member(client):
    register(client, email="ada@example.com")
    ws_id = make_workspace(client)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        r = bob.get(f"/workspaces/{ws_id}", follow_redirects=False)
        # 404 (not 403) so we don't leak that the workspace exists.
        assert r.status_code == 404


def test_join_workspace_by_code_grants_access(client):
    register(client, email="ada@example.com")
    ws_id = make_workspace(client, "Shared Space")
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        joined = bob.post("/workspaces/join", data={"code": ws_id}, follow_redirects=False)
        assert joined.status_code == 303
        assert joined.headers["location"] == f"/workspaces/{ws_id}"
        # Now a member, Bob can open the workspace.
        assert bob.get(f"/workspaces/{ws_id}").status_code == 200


def test_join_unknown_code_redirects_with_error(auth_client):
    r = auth_client.post("/workspaces/join", data={"code": "deadbeef"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=notfound" in r.headers["location"]


def test_add_surface_appears_in_workspace(auth_client):
    ws_id = make_workspace(auth_client)
    auth_client.post(
        f"/workspaces/{ws_id}/surfaces",
        data={"kind": "board", "title": "Roadmap"},
        follow_redirects=False,
    )
    body = auth_client.get(f"/workspaces/{ws_id}").text
    assert "Roadmap" in body
    # Original seeded board plus the new one → at least two boards.
    assert len(surface_ids(body, "board")) >= 2


def test_creating_a_surface_requires_membership(client):
    register(client, email="ada@example.com")
    ws_id = make_workspace(client)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        r = bob.post(
            f"/workspaces/{ws_id}/surfaces",
            data={"kind": "board", "title": "Sneaky"},
            follow_redirects=False,
        )
        assert r.status_code == 404
