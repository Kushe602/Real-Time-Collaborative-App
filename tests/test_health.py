"""The platform liveness probe used by the deploy health check."""
from __future__ import annotations


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
