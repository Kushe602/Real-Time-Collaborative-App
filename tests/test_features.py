"""Tests for the collaboration features layered on top of the base surfaces:
rich Kanban cards, card comments with @mentions, in-app notifications,
document version history + restore, and whiteboard pen/text/delete.

Mirrors :mod:`tests.test_realtime`: one socket per test, sender-inclusive
broadcasts asserted live and sender-excluded effects asserted via persistence
(an HTTP re-fetch) behind a chat "flush" ordering barrier. Cross-user delivery
(notifications target one recipient) is verified through the recipient's own
HTTP view rather than a second live socket.
"""
from __future__ import annotations

import re

from sqlalchemy import create_engine, text
from starlette.testclient import TestClient

from app.main import app
from app.realtime.handlers import _clean_checklist, _clean_labels
from app.services import parse_mentions
from tests.conftest import _SYNC_URL
from tests.helpers import invite_code, list_ids, make_workspace, register, surface_ids


def _drain_to(ws, want_type, *, channel=None, tries=12):
    """Read frames until one matches, skipping roster/join/ordering noise."""
    for _ in range(tries):
        m = ws.receive_json()
        if m.get("type") == want_type and (channel is None or m.get("channel") == channel):
            return m
    raise AssertionError(f"did not see a {want_type!r} frame within {tries} messages")


def _first_card_id(html: str) -> str:
    m = re.search(r'data-card="([0-9a-fA-F]+)"', html)
    assert m, "no card id in rendered html"
    return m.group(1)


def _user_id_by_email(email: str) -> str:
    """Read a user's id straight from the shared SQLite file (no HTTP endpoint for it)."""
    engine = create_engine(_SYNC_URL)
    try:
        with engine.begin() as conn:
            row = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).first()
    finally:
        engine.dispose()
    assert row, f"no user for {email}"
    return row[0]


def _board_ctx(client):
    """Create a workspace and return ``(ws_id, board_id, first_list_id)``."""
    ws_id = make_workspace(client)
    board_id = surface_ids(client.get(f"/workspaces/{ws_id}").text, "board")[0]
    board_html = client.get(f"/workspaces/{ws_id}/board/{board_id}").text
    return ws_id, board_id, list_ids(board_html)[0]


def _create_card(ws, board_id, list_id, title="Task"):
    ws.send_json({
        "channel": "board", "type": "card.create", "board_id": board_id,
        "list_id": list_id, "title": title, "position": 1500.0,
    })
    return _first_card_id(_drain_to(ws, "card.created", channel="board")["html"])


# --- rich cards ------------------------------------------------------------

def test_card_update_broadcasts_rich_fields(auth_client):
    ws_id, board_id, first_list = _board_ctx(auth_client)
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        card_id = _create_card(ws, board_id, first_list, "Ship it")
        ws.send_json({
            "channel": "board", "type": "card.update", "card_id": card_id,
            "patch": {
                "due_date": "2026-10-01",
                "labels": [{"text": "urgent", "color": "#ef4444"}],
                "checklist": [{"text": "draft", "done": True}, {"text": "review", "done": False}],
            },
        })
        m = _drain_to(ws, "card.updated", channel="board")
        assert m["card"]["due_date"] == "2026-10-01"
        assert [lab["text"] for lab in m["card"]["labels"]] == ["urgent"]
        assert len(m["card"]["checklist"]) == 2

    after = auth_client.get(f"/workspaces/{ws_id}/board/{board_id}").text
    assert "urgent" in after
    assert "2026-10-01" in after
    assert "1/2" in after  # one of two checklist items done

# --- comments, @mentions, and notifications --------------------------------

def test_card_assignment_notifies_the_assignee(client):
    register(client, email="ada@example.com")
    ws_id, board_id, first_list = _board_ctx(client)
    code = invite_code(client.get(f"/workspaces/{ws_id}").text)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        bob.post("/workspaces/join", data={"code": code}, follow_redirects=False)
        bob_id = _user_id_by_email("bob@example.com")

        with client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
            ws.receive_json()  # roster
            card_id = _create_card(ws, board_id, first_list, "Owned work")
            ws.send_json({
                "channel": "board", "type": "card.update",
                "card_id": card_id, "patch": {"assignee_id": bob_id},
            })
            assert _drain_to(ws, "card.updated", channel="board")["card"]["assignee_id"] == bob_id

        # The assignee gets a persisted notification naming the actor.
        bob_shell = bob.get(f"/workspaces/{ws_id}").text
        assert "assigned you to" in bob_shell
        assert "Ada Lovelace" in bob_shell

    # …and the actor does not notify herself.
    assert "assigned you to" not in client.get(f"/workspaces/{ws_id}").text


def test_comment_with_mention_notifies_member(client):
    register(client, email="ada@example.com")
    ws_id, board_id, first_list = _board_ctx(client)
    code = invite_code(client.get(f"/workspaces/{ws_id}").text)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        bob.post("/workspaces/join", data={"code": code}, follow_redirects=False)

        with client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
            ws.receive_json()  # roster
            card_id = _create_card(ws, board_id, first_list, "Discuss")
            ws.send_json({
                "channel": "board", "type": "comment.add",
                "card_id": card_id, "body": "please review @bob",
            })
            m = _drain_to(ws, "comment.added", channel="board")
            assert "cs-mention" in m["html"] and "@bob" in m["html"]

        assert "mentioned you in a comment" in bob.get(f"/workspaces/{ws_id}").text

    # The comment itself persisted on the card.
    assert "cs-mention" in client.get(f"/workspaces/{ws_id}/card/{card_id}").text


def test_self_mention_creates_no_notification(auth_client):
    ws_id, board_id, first_list = _board_ctx(auth_client)
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        card_id = _create_card(ws, board_id, first_list, "Solo")
        ws.send_json({
            "channel": "board", "type": "comment.add",
            "card_id": card_id, "body": "note to self @ada",
        })
        _drain_to(ws, "comment.added", channel="board")

    assert "mentioned you in a comment" not in auth_client.get(f"/workspaces/{ws_id}").text

# --- doc version history + restore -----------------------------------------

def test_doc_versions_snapshot_and_restore(auth_client):
    ws_id = make_workspace(auth_client)
    doc_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "doc")[0]

    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({"channel": "doc", "type": "doc.update",
                      "doc_id": doc_id, "content": "# First\n\nalpha content"})
        ws.send_json({"channel": "doc", "type": "doc.update",
                      "doc_id": doc_id, "content": "# Second\n\nbravo content"})
        ws.send_json({"channel": "chat", "type": "send", "body": "flush"})
        _drain_to(ws, "message", channel="chat")  # both doc.updates ran first

    versions = auth_client.get(f"/workspaces/{ws_id}/doc/{doc_id}/versions").text
    assert 'data-version-restore="1"' in versions
    assert 'data-version-restore="2"' in versions
    assert "alpha content" in versions  # v1 snapshot preview is shown

    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({"channel": "doc", "type": "doc.restore", "doc_id": doc_id, "version": 1})
        m = _drain_to(ws, "doc.updated", channel="doc")
        assert m["restored_from"] == 1
        assert "alpha content" in m["content"]
        assert m["version"] == 3  # restore writes the old body forward as a new version

    after = auth_client.get(f"/workspaces/{ws_id}/doc/{doc_id}").text
    assert "alpha content" in after
    assert 'data-version="3"' in after


# --- whiteboard: pen / text / delete ---------------------------------------

def test_whiteboard_path_and_text_persist(auth_client):
    ws_id = make_workspace(auth_client)
    wb_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "whiteboard")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({
            "channel": "whiteboard", "type": "element.create", "whiteboard_id": wb_id,
            "kind": "path",
            "data": {"points": [[0, 0], [10, 10], [20, 5]], "color": "#38bdf8", "size": 3},
        })
        pm = _drain_to(ws, "element.created", channel="whiteboard")
        assert pm["kind"] == "path"
        assert pm["data"]["points"][1] == [10, 10]
        path_id = pm["id"]

        ws.send_json({
            "channel": "whiteboard", "type": "element.create", "whiteboard_id": wb_id,
            "kind": "text",
            "data": {"x": 100, "y": 120, "text": "hello board", "color": "#e2e8f0", "size": 18},
        })
        tm = _drain_to(ws, "element.created", channel="whiteboard")
        assert tm["kind"] == "text"
        assert tm["data"]["text"] == "hello board"
        text_id = tm["id"]

    after = auth_client.get(f"/workspaces/{ws_id}/whiteboard/{wb_id}").text
    assert path_id in after and text_id in after
    assert "hello board" in after

def test_whiteboard_element_delete_removes_it(auth_client):
    ws_id = make_workspace(auth_client)
    wb_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "whiteboard")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({
            "channel": "whiteboard", "type": "element.create", "whiteboard_id": wb_id,
            "kind": "note", "data": {"x": 10, "y": 10, "text": "temp"},
        })
        el_id = _drain_to(ws, "element.created", channel="whiteboard")["id"]
        ws.send_json({
            "channel": "whiteboard", "type": "element.delete",
            "whiteboard_id": wb_id, "element_id": el_id,
        })
        assert _drain_to(ws, "element.deleted", channel="whiteboard")["element_id"] == el_id

    after = auth_client.get(f"/workspaces/{ws_id}/whiteboard/{wb_id}").text
    assert el_id not in after
    assert "[]" in after  # the element seed is empty again


# --- unit: sanitisers + mention parsing ------------------------------------

def test_parse_mentions_is_lowercased_and_deduped():
    assert parse_mentions("ping @Ada and @bob_smith, cc @Ada") == {"ada", "bob_smith"}


def test_clean_labels_caps_defaults_and_drops_empty():
    many = [{"text": f"lab{i}", "color": "#123456"} for i in range(15)]
    assert len(_clean_labels(many)) == 12  # capped at 12

    mixed = _clean_labels([{"text": "keep"}, {"text": "  ", "color": "#fff"}])
    assert mixed == [{"text": "keep", "color": "#6366f1"}]  # empty dropped, default colour


def test_clean_checklist_assigns_ids_and_coerces_done():
    cleaned = _clean_checklist([
        {"text": "keep", "done": "yes"},
        {"text": "  "},  # blank item dropped
        {"id": "fixed", "text": "explicit", "done": False},
    ])
    assert len(cleaned) == 2
    assert cleaned[0]["done"] is True and cleaned[0]["id"]
    assert cleaned[1]["id"] == "fixed"
