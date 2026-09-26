"""Application settings, loaded from the environment / .env via pydantic-settings."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-change-me-to-a-long-random-value-32b"
    database_url: str = "sqlite+aiosqlite:///./collabspace.db"

    # Session cookie / JWT lifetime.
    session_days: int = 7

    # Send the session cookie only over HTTPS. Keep False for plain-http local
    # dev; set COOKIE_SECURE=true behind TLS in production.
    cookie_secure: bool = False

    # Optional Redis URL (e.g. redis://localhost:6379/0). When set, WebSocket
    # broadcasts and presence fan out through Redis pub/sub so more than one app
    # process can serve the same workspace. Unset → in-memory, single-process.
    redis_url: str | None = None

    # Seconds a websocket may stay idle before the server sends a ping frame.
    ws_heartbeat_seconds: float = 25.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
