"""API test fixtures: a throwaway PostgreSQL database, migrated with Alembic.

Needs a reachable PostgreSQL (``docker compose up -d postgres``); tests are skipped otherwise.
Set ``PGROUND_TEST_ADMIN_URL`` to point elsewhere (default: the compose database server).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[3]
ADMIN_URL = os.environ.get("PGROUND_TEST_ADMIN_URL", "postgresql://pground:pground@localhost:5432/postgres")


async def _can_connect() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_URL, timeout=3)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create a fresh database, run migrations, drop it afterwards."""
    if not asyncio.run(_can_connect()):
        pytest.skip("PostgreSQL not reachable (run `docker compose up -d postgres`)")
    name = f"pground_test_{uuid.uuid4().hex[:8]}"

    async def create() -> None:
        conn = await asyncpg.connect(ADMIN_URL)
        await conn.execute(f'create database "{name}"')
        await conn.close()

    async def drop() -> None:
        conn = await asyncpg.connect(ADMIN_URL)
        await conn.execute(f'drop database if exists "{name}" with (force)')
        await conn.close()

    asyncio.run(create())
    url = ADMIN_URL.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + f"/{name}"
    os.environ["PGROUND_DATABASE_URL"] = url
    os.environ["PGROUND_CONFIG_PATH"] = str(ROOT / "provingground.yaml")
    os.environ["PGROUND_DEMO"] = "true"  # the tests use the demo fleet, whatever a local .env says
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "services/api/alembic.ini")), "head")
    from pground_api.settings import get_settings

    get_settings.cache_clear()
    yield url
    asyncio.run(drop())


@pytest_asyncio.fixture
async def db(database_url: str) -> AsyncIterator[None]:
    """A clean schema for every test."""
    from sqlalchemy import text

    from pground_api.db.session import dispose_engine, init_engine, session_scope

    init_engine(database_url)
    async with session_scope() as session:
        await session.execute(
            text("truncate agents, runs, case_results, scores, comparisons, gate_policies, gate_evaluations, datasets, "
                 "test_cases, spans, jobs, schedules, online_scores restart identity cascade")
        )  # fmt: skip
    yield
    await dispose_engine()


ADMIN = {"x-api-key": "dev-admin-key"}
INGEST = {"x-api-key": "dev-ingest-key"}


@pytest_asyncio.fixture
async def client(db: None) -> AsyncIterator[httpx.AsyncClient]:
    from pground_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=ADMIN) as c:
        yield c


@pytest_asyncio.fixture
async def anon(db: None) -> AsyncIterator[httpx.AsyncClient]:
    """A client with no default credentials."""
    from pground_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
