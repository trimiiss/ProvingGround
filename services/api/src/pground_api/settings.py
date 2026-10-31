"""Server settings, read from the environment (see ``.env.example``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PGROUND_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pground:pground@localhost:5432/pground"
    api_key: str = "dev-admin-key"
    ingest_key: str = "dev-ingest-key"
    config_path: Path = Path("provingground.yaml")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    # worker
    worker_poll_s: float = 5.0
    job_heartbeat_s: float = 10.0
    job_stale_s: float = 60.0
    schedule_tick_s: float = 30.0
    # online evaluation
    online_sample_rate: float = 0.25


@lru_cache
def get_settings() -> Settings:
    return Settings()
