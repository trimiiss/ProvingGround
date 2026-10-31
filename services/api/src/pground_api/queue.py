"""A PostgreSQL-backed job queue: ``FOR UPDATE SKIP LOCKED`` claiming, heartbeats, retries.

Workers wake instantly via ``LISTEN/NOTIFY`` and fall back to polling, so a missed notification
only costs one poll interval. Jobs whose worker stops heartbeating are reaped back to
``queued`` (or failed once attempts are exhausted).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import models as db
from .db.session import session_scope
from .settings import get_settings

CHANNEL = "pground_jobs"


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    priority: int = 0,
    run_after: datetime | None = None,
    max_attempts: int = 2,
    schedule_id: int | None = None,
) -> db.Job:
    job = db.Job(
        kind=kind,
        payload=payload,
        priority=priority,
        max_attempts=max_attempts,
        schedule_id=schedule_id,
        run_after=run_after or datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    await session.execute(text(f"select pg_notify('{CHANNEL}', :id)"), {"id": str(job.id)})
    return job


CLAIM_SQL = text(
    """
    update jobs set status = 'running', locked_by = :worker, heartbeat_at = now(),
                    started_at = coalesce(started_at, now()), attempts = attempts + 1
    where id = (
        select id from jobs
        where status = 'queued' and run_after <= now()
        order by priority desc, created_at
        for update skip locked
        limit 1
    )
    returning id
    """
)


async def claim(worker_id: str) -> db.Job | None:
    async with session_scope() as session:
        job_id = (await session.execute(CLAIM_SQL, {"worker": worker_id})).scalar_one_or_none()
        if job_id is None:
            return None
        return await session.get(db.Job, job_id)


async def heartbeat(job_id: uuid.UUID, worker_id: str, progress: float | None = None) -> bool:
    """Refresh the lease. Returns False when the job was reaped / reassigned (stop working on it)."""
    async with session_scope() as session:
        values: dict[str, Any] = {"heartbeat_at": datetime.now(UTC)}
        if progress is not None:
            values["progress"] = progress
        stmt = (
            update(db.Job)
            .where(db.Job.id == job_id, db.Job.locked_by == worker_id, db.Job.status == "running")
            .values(**values)
            .returning(db.Job.id)
        )
        return (await session.execute(stmt)).first() is not None


async def finish(job_id: uuid.UUID, result: dict[str, Any]) -> None:
    async with session_scope() as session:
        await session.execute(
            update(db.Job)
            .where(db.Job.id == job_id)
            .values(
                status="succeeded", result=result, progress=1.0, finished_at=datetime.now(UTC), error=None
            )
        )


async def fail(job_id: uuid.UUID, error: str, *, retry: bool = True) -> str:
    """Record a failure; requeue with backoff while attempts remain. Returns the new status."""
    async with session_scope() as session:
        job = await session.get(db.Job, job_id)
        if job is None:
            return "missing"
        if retry and job.attempts < job.max_attempts:
            job.status = "queued"
            job.locked_by = None
            job.error = error[:4000]
            job.run_after = datetime.now(UTC) + timedelta(seconds=min(60, 5 * 2**job.attempts))
            return "queued"
        job.status = "failed"
        job.error = error[:4000]
        job.finished_at = datetime.now(UTC)
        return "failed"


async def reap_stale(stale_s: float) -> int:
    """Requeue (or fail) running jobs whose worker stopped heartbeating."""
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_s)
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(db.Job).where(db.Job.status == "running", db.Job.heartbeat_at < cutoff)
                )
            )
            .scalars()
            .all()
        )
        for job in rows:
            if job.attempts < job.max_attempts:
                job.status, job.locked_by = "queued", None
                job.error = "worker stopped responding; requeued"
            else:
                job.status, job.finished_at = "failed", datetime.now(UTC)
                job.error = "worker stopped responding; attempts exhausted"
        return len(rows)


async def queue_depth() -> dict[str, int]:
    async with session_scope() as session:
        rows = (await session.execute(text("select status, count(*) from jobs group by status"))).all()
    return {status: int(n) for status, n in rows}


class Waker:
    """Wakes a worker when a job is enqueued (LISTEN/NOTIFY), with a polling fallback."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self._conn: asyncpg.Connection | None = None

    async def start(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
        try:
            self._conn = await asyncpg.connect(dsn)
            await self._conn.add_listener(CHANNEL, lambda *_: self.event.set())
        except Exception:
            self._conn = None

    async def wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.event.wait(), timeout)
        self.event.clear()

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
