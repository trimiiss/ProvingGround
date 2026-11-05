"""Background worker: executes evaluation / red-team / gate jobs and fires schedules."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from croniter import croniter
from sqlalchemy import select

from provingground.compare import compare_runs
from provingground.config import ProjectConfig
from provingground.engine import resolve_suites, run_target, settings_from_config
from provingground.gate import evaluate_gate
from provingground.runner import RunSettings

from . import packs, queue, store
from .db import models as db
from .db.session import init_engine, session_scope
from .deps import project_config
from .online import run_online_eval
from .routers.compare import policy_for, record_gate
from .settings import get_settings

log = logging.getLogger("pground_api.worker")

Handler = Any


class Progress:
    """Shared progress value: the runner callback writes it, the heartbeat task persists it."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self, done: int, total: int) -> None:
        self.value = done / total if total else 0.0


def config_for(payload: dict[str, Any]) -> ProjectConfig:
    """The demo config, or the uploaded folder the agent came from."""
    return packs.pack_config(payload["pack"]) if payload.get("pack") else project_config()


def code_scope(payload: dict[str, Any], cfg: ProjectConfig) -> contextlib.AbstractContextManager[None]:
    """An uploaded folder's own modules (its bot.py) stay out of the next job's imports."""
    return packs.isolated_imports(cfg.root) if payload.get("pack") else contextlib.nullcontext()


def _settings(payload: dict[str, Any]) -> RunSettings:
    cfg = config_for(payload)
    return settings_from_config(
        cfg,
        sample=payload.get("sample"),
        repeats=payload.get("repeats"),
        seed=payload.get("seed"),
        use_cache=payload.get("use_cache", True),
    )


async def fan_out(job: db.Job) -> dict[str, Any]:
    """``target: "*"`` (e.g. the nightly schedule): one job per fleet agent that has suites."""
    cfg = project_config()
    names = sorted(n for n, t in cfg.all_targets().items() if t.fleet and t.suites)
    if job.kind == "redteam":
        names = [n for n in names if any(s.startswith("security-") for s in cfg.all_targets()[n].suites)]
    async with session_scope() as session:
        for name in names:
            await queue.enqueue(session, job.kind, {**job.payload, "target": name})
    return {"enqueued": len(names), "targets": names}


async def handle_run(job: db.Job, progress: Progress) -> dict[str, Any]:
    p = job.payload
    if p.get("target") == "*":
        return await fan_out(job)
    cfg = config_for(p)
    kind = "redteam" if job.kind == "redteam" else "eval"
    suites = p.get("suites")
    if kind == "redteam" and not suites:
        suites = [
            s for s in cfg.target(p["target"]).suites if s.startswith("security-") or s == "over-refusal"
        ]
    labels = {**(p.get("labels") or {}), "kind": kind, "job": str(job.id)}
    planned = resolve_suites(cfg, cfg.target(p["target"]), suites)
    if kind == "eval" and any(s.startswith(packs.BUILTIN_PREFIX) for s in planned):
        labels["attacks"] = "builtin"  # one run feeds the gate and the Red team page, so nothing runs twice
    with code_scope(p, cfg):
        run = await run_target(
            cfg,
            p["target"],
            suites=suites,
            settings=_settings(p),
            overrides=p.get("overrides") or {},
            labels=labels,
            progress=progress,
        )
    out: dict[str, Any] = {
        "run_id": run.id, "pass_rate": run.summary.pass_rate, "n_results": run.summary.n_results,
    }  # fmt: skip
    async with session_scope() as session:
        await store.save_run(session, run)
        if p.get("gate"):  # e.g. an upload: judge the run against the folder's absolute rules too
            name = p["gate"]
            policy = cfg.gates[name] if p.get("pack") else await policy_for(session, name)
            result = evaluate_gate(policy, name, run, None)
            row = await record_gate(session, result)
            out |= {"verdict": str(result.verdict), "evaluation_id": row.id}
    return out


async def handle_gate(job: db.Job, progress: Progress) -> dict[str, Any]:
    p = job.payload
    cfg = config_for(p)
    target = p["target"]
    policy_name = p.get("policy", "release")
    settings = _settings(p)
    candidate_over = p.get("candidate_overrides") or {}
    baseline_run = None
    suites = p.get("suites")
    async with session_scope() as session:
        policy = cfg.gates[policy_name] if p.get("pack") else await policy_for(session, policy_name)
        if p.get("baseline_run_id"):
            baseline_run = await store.load_run(session, p["baseline_run_id"])
            if baseline_run is None:
                raise ValueError(f"baseline run {p['baseline_run_id']!r} not found")
            suites = [s.name for s in baseline_run.suites]
    suites = suites or resolve_suites(cfg, cfg.target(target).with_overrides(candidate_over), None)
    with code_scope(p, cfg):
        candidate = await run_target(
            cfg,
            target,
            suites=suites,
            settings=settings,
            overrides=candidate_over,
            labels={"role": "candidate", "kind": "eval", "job": str(job.id)},
            progress=progress,
        )
        if baseline_run is None:
            baseline_run = await run_target(
                cfg,
                target,
                suites=suites,
                settings=settings,
                overrides=p.get("baseline_overrides") or {},
                labels={"role": "baseline", "kind": "eval", "job": str(job.id)},
            )
    comparison = compare_runs(baseline_run, candidate, confidence=policy.confidence)
    result = evaluate_gate(policy, policy_name, candidate, comparison)
    async with session_scope() as session:
        if not await session.get(db.Run, baseline_run.id):
            await store.save_run(session, baseline_run)
        await store.save_run(session, candidate)
        row = await record_gate(session, result)
    return {
        "verdict": str(result.verdict),
        "exit_code": result.exit_code,
        "candidate_run_id": candidate.id,
        "baseline_run_id": baseline_run.id,
        "evaluation_id": row.id,
    }


async def handle_online_eval(job: db.Job, progress: Progress) -> dict[str, Any]:
    p = job.payload
    async with session_scope() as session:
        return await run_online_eval(
            session,
            hours=float(p.get("hours", 1)),
            rate=float(p.get("rate", 0.2)),
            limit=int(p.get("limit", 300)),
        )


HANDLERS: dict[str, Handler] = {
    "run": handle_run,
    "redteam": handle_run,
    "gate": handle_gate,
    "online_eval": handle_online_eval,
}


async def execute(job: db.Job, worker_id: str) -> None:
    progress = Progress()
    settings = get_settings()

    async def beat() -> None:
        while True:
            await asyncio.sleep(settings.job_heartbeat_s)
            if not await queue.heartbeat(job.id, worker_id, progress.value):
                log.warning("lost lease on job %s", job.id)
                return

    heartbeat_task = asyncio.create_task(beat())
    try:
        handler = HANDLERS.get(job.kind)
        if handler is None:
            raise ValueError(f"no handler for job kind {job.kind!r}")
        result = await handler(job, progress)
        await queue.finish(job.id, result)
        log.info("job %s (%s) succeeded", job.id, job.kind)
    except Exception as exc:
        status = await queue.fail(job.id, f"{type(exc).__name__}: {exc}")
        log.exception("job %s failed (%s)", job.id, status)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def fire_due_schedules() -> int:
    """Enqueue a job for every enabled schedule whose time has come, then advance it."""
    fired = 0
    now = datetime.now(UTC)
    async with session_scope() as session:
        due = (
            (
                await session.execute(
                    select(db.Schedule)
                    .where(db.Schedule.enabled.is_(True), db.Schedule.next_run_at <= now)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for sched in due:
            await queue.enqueue(session, sched.job_kind, sched.payload, schedule_id=sched.id)
            sched.last_run_at = now
            sched.next_run_at = croniter(sched.cron, now).get_next(datetime)
            fired += 1
    return fired


async def maintenance_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await fire_due_schedules()
            reaped = await queue.reap_stale(settings.job_stale_s)
            if reaped:
                log.warning("reaped %d stale job(s)", reaped)
        except Exception:
            log.exception("maintenance tick failed")
        await asyncio.sleep(settings.schedule_tick_s)


async def run_worker(worker_id: str | None = None) -> None:
    init_engine()
    worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    settings = get_settings()
    waker = queue.Waker()
    await waker.start()
    maintenance = asyncio.create_task(maintenance_loop())
    log.info("worker %s started", worker_id)
    try:
        while True:
            job = await queue.claim(worker_id)
            if job is None:
                await waker.wait(settings.worker_poll_s)
                continue
            await execute(job, worker_id)
    finally:
        maintenance.cancel()
        await waker.stop()
