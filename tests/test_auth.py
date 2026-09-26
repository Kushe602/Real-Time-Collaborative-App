"""Auth flows: registration, login, logout, and the protected-route redirect."""
from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app
from tests.helpers import login, register


def test_landing_page_ok(client):
    assert client.get("/").status_code == 200


def test_login_page_ok(client):
    assert client.get("/login").status_code == 200


def test_register_sets_cookie_and_redirects(client):
    r = register(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "access_token" in client.cookies


def test_register_rejects_short_password(client):
    r = register(client, password="short")
    assert r.status_code == 400
    assert "access_token" not in client.cookies


def test_register_rejects_malformed_email(client):
    r = register(client, email="not-an-email")
    assert r.status_code == 400


def test_register_rejects_duplicate_email(client):
    assert register(client).status_code == 303
    r = register(client, display_name="Someone Else")
    assert r.status_code == 400


def test_login_succeeds_with_correct_password(client):
    register(client, email="grace@example.com", password="supersecret123")
    client.cookies.clear()  # force a real login rather than riding the register cookie
    r = login(client, email="grace@example.com", password="supersecret123")
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "access_token" in client.cookies


def test_login_rejects_wrong_password(client):
    register(client, email="grace@example.com", password="supersecret123")
    client.cookies.clear()
    r = login(client, email="grace@example.com", password="wrongpassword")
    assert r.status_code == 400
    assert "access_token" not in client.cookies


def test_login_is_case_insensitive_on_email(client):
    register(client, email="grace@example.com", password="supersecret123")
    client.cookies.clear()
    r = login(client, email="GRACE@example.com", password="supersecret123")
    assert r.status_code == 303


def test_dashboard_requires_auth(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_dashboard_ok_when_authenticated(auth_client):
    assert auth_client.get("/dashboard").status_code == 200


def test_logout_drops_the_session(auth_client):
    assert auth_client.post("/logout", follow_redirects=False).status_code == 303
    after = auth_client.get("/dashboard", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"] == "/login"


def test_authed_user_visiting_login_is_bounced_to_dashboard(auth_client):
    r = auth_client.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_two_clients_have_independent_sessions(client):
    register(client, email="ada@example.com")
    with TestClient(app) as anon:
        # A brand-new client has no cookie and stays anonymous.
        assert anon.get("/dashboard", follow_redirects=False).status_code == 303
