"""Test configuration: point the app at a throwaway SQLite file, then rebuild the
schema before every test so each one runs against a clean database.

Environment variables are set *before* importing anything from :mod:`app`, because
settings (and the async engine) are constructed at import time.
"""
import os
import pathlib

_DB_PATH = pathlib.Path(__file__).resolve().parents[1] / "test_collabspace.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-000000000000"

import pytest
from sqlalchemy import create_engine
from starlette.testclient import TestClient

import app.models  # noqa: F401 - ensure models register on Base.metadata
from app.database import Base
from app.database import engine as async_engine
from app.main import app

_SYNC_URL = f"sqlite:///{_DB_PATH.as_posix()}"


@pytest.fixture(autouse=True)
def fresh_db():
    """Drop and recreate every table around each test for full isolation.

    Each ``TestClient`` starts its own event loop, so we also abandon the async
    engine's pooled connections (``close=False`` just drops them from the pool,
    without touching the now-dead loop they were created on) — otherwise the next
    test would reuse an aiosqlite connection bound to a stale loop.
    """
    async_engine.sync_engine.dispose(close=False)
    engine = create_engine(_SYNC_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    """A client with a freshly registered, logged-in user (Ada)."""
    from tests.helpers import register

    register(client)
    return client
