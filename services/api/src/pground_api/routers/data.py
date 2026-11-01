"""Datasets (incl. incident capture), schedules, the model leaderboard, config and sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from croniter import croniter
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import Text, cast, func, select, text

from provingground.datasets import load_jsonl
from provingground.models import TestCase

from ..db import models as db
from ..deps import Admin, Session, project_config
from ..schemas import CaseIn, ConfigOut, DatasetOut, ModelStats, ScheduleIn, ScheduleOut

router = APIRouter(prefix="/v1", tags=["data"], dependencies=[Admin])

INCIDENTS = "incidents"


# --- datasets --------------------------------------------------------------------------------


async def dataset_row(session: Session, name: str, *, create: bool = False) -> db.Dataset:
    row = (await session.execute(select(db.Dataset).where(db.Dataset.name == name))).scalar_one_or_none()
    if row is None:
        if not create:
            raise HTTPException(404, f"dataset {name!r} not found")
        row = db.Dataset(
            name=name,
            description="Regression cases captured from production incidents." if name == INCIDENTS else "",
        )
        session.add(row)
        await session.flush()
    return row


@router.get("/datasets", response_model=list[DatasetOut])
async def list_datasets(session: Session) -> list[DatasetOut]:
    rows = (
        await session.execute(
            select(db.Dataset, func.count(db.TestCaseRow.id))
            .outerjoin(db.TestCaseRow)
            .group_by(db.Dataset.id)
            .order_by(db.Dataset.name)
        )
    ).all()
    return [
        DatasetOut(name=d.name, description=d.description, source=d.source, version=d.version, n_cases=n)
        for d, n in rows
    ]


@router.get("/datasets/{name}/cases")
async def dataset_cases(
    name: str,
    session: Session,
    tag: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    ds = await dataset_row(session, name)
    stmt = select(db.TestCaseRow).where(db.TestCaseRow.dataset_id == ds.id)
    if tag:
        stmt = stmt.where(db.TestCaseRow.payload["tags"].contains([tag]))
    if q:
        stmt = stmt.where(cast(db.TestCaseRow.payload, Text).ilike(f"%{q}%"))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await session.execute(stmt.order_by(db.TestCaseRow.id).limit(limit).offset(offset))).scalars().all()
    )
    return {"items": [r.payload for r in rows], "total": total}


@router.post("/datasets/{name}/cases", status_code=201)
async def add_case(name: str, body: CaseIn, session: Session) -> dict[str, Any]:
    ds = await dataset_row(session, name, create=True)
    case_id = body.case_id or f"{name}-{int(datetime.now(UTC).timestamp())}"
    case = TestCase(
        id=case_id,
        input=body.input,
        messages=body.messages,
        expected=body.expected,
        context=body.context,
        tags=body.tags,
        critical=body.critical,
        metadata=body.metadata,
    )
    exists = (
        await session.execute(
            select(db.TestCaseRow).where(
                db.TestCaseRow.dataset_id == ds.id, db.TestCaseRow.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, f"case {case_id!r} already exists in {name!r}")
    session.add(
        db.TestCaseRow(
            dataset_id=ds.id, case_id=case_id, payload=case.model_dump(mode="json", exclude_defaults=True)
        )
    )
    return case.model_dump(mode="json", exclude_defaults=True)


@router.get("/datasets/{name}/export")
async def export_dataset(name: str, session: Session) -> Response:
    ds = await dataset_row(session, name)
    rows = (
        (
            await session.execute(
                select(db.TestCaseRow.payload)
                .where(db.TestCaseRow.dataset_id == ds.id)
                .order_by(db.TestCaseRow.id)
            )
        )
        .scalars()
        .all()
    )
    body = "\n".join(json.dumps(p, ensure_ascii=False) for p in rows) + "\n"
    return Response(
        body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}.jsonl"'},
    )


# --- schedules -------------------------------------------------------------------------------


def schedule_out(s: db.Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        name=s.name,
        cron=s.cron,
        job_kind=s.job_kind,
        payload=s.payload,
        enabled=s.enabled,
        last_run_at=s.last_run_at,
        next_run_at=s.next_run_at,
    )


@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(session: Session) -> list[ScheduleOut]:
    return [
        schedule_out(s)
        for s in (await session.execute(select(db.Schedule).order_by(db.Schedule.name))).scalars()
    ]


@router.put("/schedules/{name}", response_model=ScheduleOut)
async def upsert_schedule(name: str, body: ScheduleIn, session: Session) -> ScheduleOut:
    if not croniter.is_valid(body.cron):
        raise HTTPException(422, f"invalid cron expression {body.cron!r}")
    row = (await session.execute(select(db.Schedule).where(db.Schedule.name == name))).scalar_one_or_none()
    nxt = croniter(body.cron, datetime.now(UTC)).get_next(datetime)
    if row is None:
        row = db.Schedule(
            name=name,
            cron=body.cron,
            job_kind=body.job_kind,
            payload=body.payload,
            enabled=body.enabled,
            next_run_at=nxt,
        )
        session.add(row)
    else:
        row.cron, row.job_kind, row.payload, row.enabled, row.next_run_at = (
            body.cron,
            body.job_kind,
            body.payload,
            body.enabled,
            nxt,
        )
    await session.flush()
    return schedule_out(row)


@router.delete("/schedules/{name}", status_code=204)
async def delete_schedule(name: str, session: Session) -> Response:
    row = (await session.execute(select(db.Schedule).where(db.Schedule.name == name))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "schedule not found")
    await session.delete(row)
    return Response(status_code=204)


# --- models leaderboard ----------------------------------------------------------------------

MODEL_METRICS = (
    "quality.correctness",
    "factuality.faithfulness",
    "factuality.hallucination_rate",
    "safety.attack_success_rate",
    "safety.false_refusal_rate",
    "perf.latency_p95_ms",
)

MODELS_SQL = text(
    """
    select model, array_agg(distinct agent) as agents, count(*) as runs,
           avg((metrics->>'quality.correctness')::float) as correctness,
           avg((metrics->>'factuality.faithfulness')::float) as faithfulness,
           avg((metrics->>'factuality.hallucination_rate')::float) as hallucination,
           avg((metrics->>'safety.attack_success_rate')::float) as asr,
           avg((metrics->>'safety.false_refusal_rate')::float) as false_refusal,
           avg((metrics->>'perf.latency_p95_ms')::float) as p95,
           avg((metrics->>'cost.per_1k_requests_usd')::float) as cost
    from runs where kind = 'eval' and model is not null and created_at > now() - make_interval(days => :days)
    group by model order by model
    """
)
LIVE_SQL = text(
    """
    select model, count(*) as requests, percentile_cont(0.95) within group (order by duration_ms) as p95
    from spans where source = 'live' and operation = 'chat' and model is not null
      and start_time > now() - interval '24 hours' group by model
    """
)


@router.get("/models", response_model=list[ModelStats])
async def models(session: Session, days: Annotated[int, Query(ge=1, le=365)] = 30) -> list[ModelStats]:
    rows = (await session.execute(MODELS_SQL, {"days": days})).all()
    live = {m: (int(n), float(p)) for m, n, p in (await session.execute(LIVE_SQL)).all()}
    out: list[ModelStats] = []
    for model, agents, runs, corr, faith, hall, asr, fr, p95, cost in rows:
        metrics = {
            k: float(v)
            for k, v in (
                ("quality.correctness", corr),
                ("factuality.faithfulness", faith),
                ("factuality.hallucination_rate", hall),
                ("safety.attack_success_rate", asr),
                ("safety.false_refusal_rate", fr),
                ("perf.latency_p95_ms", p95),
            )
            if v is not None
        }
        short = model.split("/", 1)[-1]
        n, lp95 = live.get(short, live.get(model, (0, None)))
        out.append(
            ModelStats(
                model=model,
                agents=sorted(agents),
                runs=int(runs),
                metrics=metrics,
                cost_per_1k_usd=float(cost) if cost is not None else None,
                traffic_requests=n,
                traffic_p95_ms=lp95,
            )
        )
    for m in out:  # Pareto front on (quality up, cost down)
        q, c = (
            m.metrics.get("quality.correctness", 0.0),
            m.cost_per_1k_usd if m.cost_per_1k_usd is not None else 0.0,
        )
        m.on_pareto_front = not any(
            o is not m
            and o.metrics.get("quality.correctness", 0.0) >= q
            and (o.cost_per_1k_usd or 0.0) <= c
            and (o.metrics.get("quality.correctness", 0.0) > q or (o.cost_per_1k_usd or 0.0) < c)
            for o in out
        )
    return out


# --- config & sync ---------------------------------------------------------------------------


@router.get("/config", response_model=ConfigOut)
async def get_config() -> ConfigOut:
    cfg = project_config()
    return ConfigOut(
        project=cfg.project,
        targets=[
            {"name": n, "kind": t.kind, "model": t.model, "suites": t.suites, "description": t.description}
            for n, t in sorted(cfg.all_targets().items())
        ],
        suites=[
            {"name": n, "description": s.description, "scorers": [sc.name for sc in s.scorers]}
            for n, s in sorted(cfg.suites.items())
        ],
        gates=sorted(cfg.gates),
        providers=sorted(cfg.providers),
    )


@router.post("/admin/sync")
async def sync_from_config(session: Session) -> dict[str, int]:
    """Mirror agents, gate policies and repo datasets from provingground.yaml into the database."""
    return await sync_config(session)


async def sync_config(session: Session) -> dict[str, int]:
    cfg = project_config()
    counts = {"agents": 0, "policies": 0, "datasets": 0, "cases": 0}
    existing = {a.slug: a for a in (await session.execute(select(db.Agent))).scalars()}
    for slug, tc in cfg.all_targets().items():
        if not tc.fleet:
            continue
        fields: dict[str, Any] = dict(
            name=tc.display_name or slug,
            team=tc.team or "",
            runtime=tc.runtime or tc.kind,
            model=tc.model,
            description=tc.description,
            suites=tc.suites,
            config={},
        )
        if slug in existing:
            for k, v in fields.items():
                setattr(existing[slug], k, v)
        else:
            session.add(db.Agent(slug=slug, **fields))
        counts["agents"] += 1
    in_fleet = {n for n, t in cfg.all_targets().items() if t.fleet}
    for slug in [
        s for s in existing if s not in in_fleet
    ]:  # removed from the config: leaves the fleet (history stays)
        await session.delete(existing[slug])
    known = {p.name: p for p in (await session.execute(select(db.GatePolicyRow))).scalars()}
    for name, policy in cfg.gates.items():
        if name not in known:
            session.add(
                db.GatePolicyRow(
                    name=name, description=policy.description, policy=policy.model_dump(mode="json")
                )
            )
            counts["policies"] += 1
    # Schedules declared in the config are created once; after that the database owns them, so a
    # schedule paused from the API or dashboard stays paused across syncs.
    scheduled = {s.name for s in (await session.execute(select(db.Schedule))).scalars()}
    counts["schedules"] = 0
    for name, sched in cfg.schedules.items():
        if name in scheduled:
            continue
        session.add(
            db.Schedule(
                name=name,
                cron=sched.cron,
                job_kind=sched.job,
                payload=sched.payload,
                enabled=sched.enabled,
                next_run_at=croniter(sched.cron, datetime.now(UTC)).get_next(datetime),
            )
        )
        counts["schedules"] += 1
    have = {d.name for d in (await session.execute(select(db.Dataset))).scalars()}
    for name, suite in cfg.suites.items():
        if name in have or not suite.dataset:
            continue
        path = cfg.resolve_path(suite.dataset)
        if not path.exists():
            continue
        cases, version = load_jsonl(path)
        ds = db.Dataset(name=name, description=suite.description, source=suite.dataset, version=version)
        session.add(ds)
        await session.flush()
        for start in range(0, len(cases), 500):
            session.add_all(
                db.TestCaseRow(
                    dataset_id=ds.id, case_id=c.id, payload=c.model_dump(mode="json", exclude_defaults=True)
                )
                for c in cases[start : start + 500]
            )
        counts["datasets"] += 1
        counts["cases"] += len(cases)
    return counts
