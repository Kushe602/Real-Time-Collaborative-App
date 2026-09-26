"""Realtime WebSocket: auth gating, presence roster, chat, and live surface mutations.

All assertions use a single socket per test. TestClient runs each connection on its
own event-loop portal, so we deliberately avoid cross-connection broadcast assertions
(which a single-process deployment handles but two TestClient portals cannot model).
Sender-inclusive broadcasts (chat, card.created, element.created) and server-side
persistence are what we verify here.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.realtime.handlers import _clean_element_data
from tests.helpers import list_ids, make_workspace, register, surface_ids


def _drain_to(ws, want_type, *, channel=None, tries=10):
    """Read frames until one matches, skipping roster/ping/join noise."""
    for _ in range(tries):
        m = ws.receive_json()
        if m.get("type") == want_type and (channel is None or m.get("channel") == channel):
            return m
    raise AssertionError(f"did not see a {want_type!r} frame within {tries} messages")


# --- auth gating -----------------------------------------------------------

def test_ws_rejects_anonymous(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/workspace/whatever") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_rejects_non_member(client):
    register(client, email="ada@example.com")
    ws_id = make_workspace(client)
    with TestClient(app) as bob:
        register(bob, display_name="Bob Stone", email="bob@example.com")
        with pytest.raises(WebSocketDisconnect) as exc:
            with bob.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
                ws.receive_json()
        assert exc.value.code == 4401


# --- presence + chat -------------------------------------------------------

def test_ws_sends_roster_on_connect(auth_client):
    ws_id = make_workspace(auth_client)
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        first = ws.receive_json()
        assert first["channel"] == "presence"
        assert first["type"] == "roster"
        assert first["me"]["display_name"] == "Ada Lovelace"
        assert any(m["user_id"] == first["me"]["user_id"] for m in first["members"])


def test_ws_chat_roundtrip(auth_client):
    ws_id = make_workspace(auth_client)
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({"channel": "chat", "type": "send", "body": "hello team"})
        m = _drain_to(ws, "message", channel="chat")
        assert m["body"] == "hello team"
        assert m["display_name"] == "Ada Lovelace"


def test_ws_blank_chat_is_ignored(auth_client):
    ws_id = make_workspace(auth_client)
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({"channel": "chat", "type": "send", "body": "   "})
        # A blank body produces no broadcast; a real message afterwards proves the
        # socket is still healthy and that nothing was queued from the blank send.
        ws.send_json({"channel": "chat", "type": "send", "body": "real one"})
        m = _drain_to(ws, "message", channel="chat")
        assert m["body"] == "real one"


# --- board mutations -------------------------------------------------------

def test_ws_card_create_broadcasts_and_persists(auth_client):
    ws_id = make_workspace(auth_client)
    shell = auth_client.get(f"/workspaces/{ws_id}").text
    board_id = surface_ids(shell, "board")[0]
    board_html = auth_client.get(f"/workspaces/{ws_id}/board/{board_id}").text
    first_list = list_ids(board_html)[0]

    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({
            "channel": "board", "type": "card.create",
            "board_id": board_id, "list_id": first_list,
            "title": "Ship the thing", "position": 1500.0,
        })
        m = _drain_to(ws, "card.created", channel="board")
        assert m["list_id"] == first_list
        assert "Ship the thing" in m["html"]

    # Persisted: a fresh HTTP fetch of the board shows the new card.
    after = auth_client.get(f"/workspaces/{ws_id}/board/{board_id}").text
    assert "Ship the thing" in after


def test_ws_card_create_ignored_for_foreign_list(auth_client):
    ws_id = make_workspace(auth_client)
    board_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "board")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        # A list id that doesn't exist must not create anything…
        ws.send_json({
            "channel": "board", "type": "card.create",
            "board_id": board_id, "list_id": "deadbeef",
            "title": "orphan", "position": 1000.0,
        })
        # …and a subsequent valid message still flows (the bad one was dropped silently).
        ws.send_json({"channel": "chat", "type": "send", "body": "ping"})
        assert _drain_to(ws, "message", channel="chat")["body"] == "ping"

    board_html = auth_client.get(f"/workspaces/{ws_id}/board/{board_id}").text
    assert "orphan" not in board_html


# --- doc mutations ---------------------------------------------------------

def test_ws_doc_update_persists_and_bumps_version(auth_client):
    ws_id = make_workspace(auth_client)
    doc_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "doc")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        # The sender is excluded from the doc broadcast, so we assert via persistence.
        ws.send_json({
            "channel": "doc", "type": "doc.update",
            "doc_id": doc_id, "content": "## Rewritten\n\nfresh content",
        })
        ws.send_json({"channel": "chat", "type": "send", "body": "flush"})
        _drain_to(ws, "message", channel="chat")  # ordering barrier: doc.update ran first

    after = auth_client.get(f"/workspaces/{ws_id}/doc/{doc_id}").text
    assert "fresh content" in after
    assert 'data-version="1"' in after


# --- whiteboard mutations --------------------------------------------------

def test_ws_element_create_broadcasts_and_persists(auth_client):
    ws_id = make_workspace(auth_client)
    wb_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "whiteboard")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({
            "channel": "whiteboard", "type": "element.create",
            "whiteboard_id": wb_id, "kind": "note",
            "data": {"x": 40, "y": 60, "w": 168, "h": 128, "text": "idea", "color": "#fde68a"},
        })
        m = _drain_to(ws, "element.created", channel="whiteboard")
        assert m["kind"] == "note"
        assert m["data"]["text"] == "idea"
        el_id = m["id"]

    after = auth_client.get(f"/workspaces/{ws_id}/whiteboard/{wb_id}").text
    assert el_id in after


def test_ws_element_create_rejects_unknown_kind(auth_client):
    ws_id = make_workspace(auth_client)
    wb_id = surface_ids(auth_client.get(f"/workspaces/{ws_id}").text, "whiteboard")[0]
    with auth_client.websocket_connect(f"/ws/workspace/{ws_id}") as ws:
        ws.receive_json()  # roster
        ws.send_json({
            "channel": "whiteboard", "type": "element.create",
            "whiteboard_id": wb_id, "kind": "malicious", "data": {"x": 1, "y": 1},
        })
        ws.send_json({"channel": "chat", "type": "send", "body": "ping"})
        assert _drain_to(ws, "message", channel="chat")["body"] == "ping"

    after = auth_client.get(f"/workspaces/{ws_id}/whiteboard/{wb_id}").text
    assert "[]" in after  # nothing was persisted


# --- unit: element-data sanitiser -----------------------------------------

def test_clean_element_data_coerces_and_whitelists():
    cleaned = _clean_element_data(
        {"x": "12.5", "y": 3, "w": "bad", "color": "#abcdef", "text": 99, "evil": "drop me"}
    )
    assert cleaned["x"] == 12.5
    assert cleaned["y"] == 3.0
    assert "w" not in cleaned  # non-numeric coercion is dropped
    assert cleaned["color"] == "#abcdef"
    assert cleaned["text"] == "99"  # coerced to str
    assert "evil" not in cleaned  # unknown keys are stripped


def test_clean_element_data_truncates_long_text():
    cleaned = _clean_element_data({"text": "x" * 5000, "color": "c" * 50})
    assert len(cleaned["text"]) == 2000
    assert len(cleaned["color"]) == 9
