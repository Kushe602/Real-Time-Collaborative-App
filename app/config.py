"""Application settings, loaded from the environment / .env via pydantic-settings."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-change-me-to-a-long-random-value-32b"
    database_url: str = "sqlite+aiosqlite:///./collabspace.db"

    # Session cookie / JWT lifetime.
    session_days: int = 7

    # Seconds a websocket may stay idle before the server sends a ping frame.
    ws_heartbeat_seconds: float = 25.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
