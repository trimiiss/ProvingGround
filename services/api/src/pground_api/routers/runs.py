"""Runs, results, reports and background jobs."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, or_, select

from provingground.models import Run as RunModel
from provingground.redteam.report import summarize_attacks
from provingground.report import render_run_reports

from .. import queue, store
from ..db import models as db
from ..deps import Admin, Session, project_config
from ..schemas import JobIn, JobOut, Page, ResultBrief, ResultDetail, RunBrief, RunDetail

router = APIRouter(prefix="/v1", tags=["runs"], dependencies=[Admin])


def run_brief(r: db.Run) -> RunBrief:
    return RunBrief(
        id=r.id,
        agent=r.agent,
        model=r.model,
        kind=r.kind,
        created_at=r.created_at,
        n_results=r.n_results,
        pass_rate=r.pass_rate,
        cost_usd=r.cost_usd,
        labels=r.labels,
        metrics={k: float(v) for k, v in (r.metrics or {}).items() if v is not None},
    )


def run_detail(r: db.Run) -> RunDetail:
    return RunDetail(
        **run_brief(r).model_dump(),
        summary=r.summary,
        suites=r.suites,
        scorer_versions=r.scorer_versions,
        settings=r.settings,
        git=r.git,
        target=r.target,
        judge_model=r.judge_model,
    )


@router.get("/runs", response_model=Page[RunBrief])
async def list_runs(
    session: Session,
    agent: str | None = None,
    kind: str | None = None,
    model: str | None = None,
    label: Annotated[str | None, Query(description="filter by a run label, as key=value")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunBrief]:
    stmt = select(db.Run)
    if agent:
        stmt = stmt.where(db.Run.agent == agent)
    if kind:
        stmt = stmt.where(db.Run.kind == kind)
    if model:
        stmt = stmt.where(db.Run.model == model)
    if label and "=" in label:
        key, value = label.split("=", 1)
        stmt = stmt.where(db.Run.labels[key].astext == value)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await session.execute(stmt.order_by(db.Run.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page(items=[run_brief(r) for r in rows], total=total)


@router.post("/runs", response_model=RunBrief, status_code=201)
async def import_run(run: RunModel, session: Session) -> RunBrief:
    """Import a run produced elsewhere (e.g. `pground push` from CI)."""
    await store.save_run(session, run)
    row = await session.get(db.Run, run.id)
    assert row is not None
    return run_brief(row)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: Session) -> RunDetail:
    row = await session.get(db.Run, run_id)
    if row is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return run_detail(row)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, session: Session) -> Response:
    row = await session.get(db.Run, run_id)
    if row is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    await session.delete(row)
    return Response(status_code=204)


def result_brief(r: db.CaseResultRow) -> ResultBrief:
    return ResultBrief(
        id=r.id,
        run_id=r.run_id,
        suite=r.suite,
        case_id=r.case_id,
        repeat=r.repeat,
        status=r.status,
        passed=r.passed,
        critical=r.critical,
        tags=r.tags,
        input_preview=r.input_preview,
        output_text=r.output_text,
        latency_ms=r.latency_ms,
        cost_usd=r.cost_usd,
        trace_id=r.trace_id,
    )


@router.get("/runs/{run_id}/results", response_model=Page[ResultBrief])
async def list_results(
    run_id: str,
    session: Session,
    suite: str | None = None,
    status: str | None = None,
    passed: bool | None = None,
    critical: bool | None = None,
    metric_failed: Annotated[
        str | None, Query(description="only results where this metric did not pass")
    ] = None,
    q: Annotated[str | None, Query(description="text search over input and output")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ResultBrief]:
    stmt = select(db.CaseResultRow).where(db.CaseResultRow.run_id == run_id)
    if suite:
        stmt = stmt.where(db.CaseResultRow.suite == suite)
    if status:
        stmt = stmt.where(db.CaseResultRow.status == status)
    if passed is not None:
        stmt = stmt.where(db.CaseResultRow.passed.is_(passed))
    if critical is not None:
        stmt = stmt.where(db.CaseResultRow.critical.is_(critical))
    if metric_failed:
        failing = select(db.ScoreRow.result_id).where(
            db.ScoreRow.run_id == run_id, db.ScoreRow.metric == metric_failed, db.ScoreRow.passed.is_(False)
        )
        stmt = stmt.where(db.CaseResultRow.id.in_(failing))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                db.CaseResultRow.input_preview.ilike(like),
                db.CaseResultRow.output_text.ilike(like),
                db.CaseResultRow.case_id.ilike(like),
            )
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(
                    db.CaseResultRow.critical.desc(),
                    db.CaseResultRow.passed.asc().nulls_last(),
                    db.CaseResultRow.id,
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page(items=[result_brief(r) for r in rows], total=total)


@router.get("/results/{result_id}", response_model=ResultDetail)
async def get_result(result_id: int, session: Session) -> ResultDetail:
    row = await session.get(db.CaseResultRow, result_id)
    if row is None:
        raise HTTPException(404, "result not found")
    return ResultDetail(**result_brief(row).model_dump(), payload=row.payload)


@router.get("/runs/{run_id}/report")
async def run_report(
    run_id: str, session: Session, format: Annotated[str, Query(pattern="^(md|html|json)$")] = "html"
) -> Response:
    run = await store.load_run(session, run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    reports = render_run_reports(run, requests_per_month=project_config().traffic.requests_per_month)
    media = {
        "md": ("report.md", "text/markdown"),
        "html": ("report.html", "text/html"),
        "json": ("run.json", "application/json"),
    }
    key, content_type = media[format]
    return Response(reports[key], media_type=f"{content_type}; charset=utf-8")


@router.get("/runs/{run_id}/redteam")
async def run_redteam(run_id: str, session: Session) -> dict[str, Any]:
    run = await store.load_run(session, run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return summarize_attacks(run).model_dump(mode="json")


# --- jobs ------------------------------------------------------------------------------------


def job_out(j: db.Job) -> JobOut:
    return JobOut(
        id=str(j.id),
        kind=j.kind,
        status=j.status,
        payload=j.payload,
        progress=j.progress,
        result=j.result,
        error=j.error,
        attempts=j.attempts,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
    )


@router.post("/jobs", response_model=JobOut, status_code=202)
async def create_job(body: JobIn, session: Session) -> JobOut:
    cfg = project_config()
    target = body.payload.get("target")
    fleet_wide = body.kind == "online_eval" or (target == "*" and body.kind in ("run", "redteam"))
    if not fleet_wide and (not target or target not in cfg.all_targets()):
        raise HTTPException(422, f"unknown target {target!r}; known: {sorted(cfg.all_targets())}")
    if body.kind == "gate" and body.payload.get("policy", "release") not in cfg.gates:
        raise HTTPException(422, f"unknown gate policy {body.payload.get('policy')!r}")
    job = await queue.enqueue(session, body.kind, body.payload, priority=body.priority)
    return job_out(job)


@router.get("/jobs", response_model=Page[JobOut])
async def list_jobs(
    session: Session,
    status: str | None = None,
    kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> Page[JobOut]:
    stmt = select(db.Job)
    if status:
        stmt = stmt.where(db.Job.status == status)
    if kind:
        stmt = stmt.where(db.Job.kind == kind)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(db.Job.created_at.desc()).limit(limit))).scalars().all()
    return Page(items=[job_out(j) for j in rows], total=total)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, session: Session) -> JobOut:
    job = await session.get(db.Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job_out(job)
