"""Surface partials: the HTML fragments the shell fetches over HTTP when a surface opens."""
from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app
from tests.helpers import make_workspace, register, surface_ids


def _seeded_ids(client):
    """Create a workspace and return (ws_id, board_id, doc_id, whiteboard_id)."""
    ws_id = make_workspace(client)
    shell = client.get(f"/workspaces/{ws_id}").text
    return (
        ws_id,
        surface_ids(shell, "board")[0],
        surface_ids(shell, "doc")[0],
        surface_ids(shell, "whiteboard")[0],
    )


def test_board_partial_lists_seeded_columns(auth_client):
    ws_id, board_id, _, _ = _seeded_ids(auth_client)
    r = auth_client.get(f"/workspaces/{ws_id}/board/{board_id}")
    assert r.status_code == 200
    assert "To do" in r.text
    assert "In progress" in r.text
    assert "Done" in r.text


def test_doc_partial_contains_seeded_content(auth_client):
    ws_id, _, doc_id, _ = _seeded_ids(auth_client)
    r = auth_client.get(f"/workspaces/{ws_id}/doc/{doc_id}")
    assert r.status_code == 200
    assert "Welcome to your workspace" in r.text


def test_whiteboard_partial_starts_empty(auth_client):
    ws_id, _, _, wb_id = _seeded_ids(auth_client)
    r = auth_client.get(f"/workspaces/{ws_id}/whiteboard/{wb_id}")
    assert r.status_code == 200
    # The element seed is an empty JSON array.
    assert "data-wb-elements" in r.text
    assert "[]" in r.text


def test_unknown_surface_id_returns_404(auth_client):
    ws_id, _, _, _ = _seeded_ids(auth_client)
    assert auth_client.get(f"/workspaces/{ws_id}/board/deadbeef").status_code == 404
    assert auth_client.get(f"/workspaces/{ws_id}/doc/deadbeef").status_code == 404
    assert auth_client.get(f"/workspaces/{ws_id}/whiteboard/deadbeef").status_code == 404


def test_surface_partial_requires_membership(client):
    register(client, email="ada@example.com")
    ws_id, board_id, _, _ = _seeded_ids(client)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        r = bob.get(f"/workspaces/{ws_id}/board/{board_id}", follow_redirects=False)
        assert r.status_code == 404
