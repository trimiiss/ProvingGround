"""Auth and shared dependencies. Single tenant: an admin key and a separate, narrower ingest key."""

from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from provingground.config import ProjectConfig, load_config

from .db.session import get_session
from .settings import Settings, get_settings

Session = Annotated[AsyncSession, Depends(get_session)]


def _matches(provided: str | None, expected: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())  # type: ignore[union-attr]


def require_admin(
    x_api_key: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)
) -> None:
    if not _matches(x_api_key, settings.api_key):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"}
        )


def require_ingest(
    x_api_key: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)
) -> None:
    """Trace ingest accepts the ingest key (agents) or the admin key (operators)."""
    if not (_matches(x_api_key, settings.ingest_key) or _matches(x_api_key, settings.api_key)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid ingest key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


Admin = Depends(require_admin)
Ingest = Depends(require_ingest)


@lru_cache
def project_config() -> ProjectConfig:
    """The repo's provingground.yaml (targets, suites, gate policies), or nothing with ``demo`` off."""
    settings = get_settings()
    if not settings.demo:
        cfg = ProjectConfig(project="uploads")
        cfg._root = settings.packs_dir
        return cfg
    return load_config(settings.config_path)
