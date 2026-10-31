"""Fleet overview, agents, trends and live-traffic aggregates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, text

from ..db import models as db
from ..deps import Admin, Session
from ..schemas import (
    AgentHealth,
    AgentOut,
    Overview,
    OverviewTotals,
    RunBrief,
    Traffic,
    TrafficBucket,
    TrendPoint,
)
from .compare import gate_brief
from .runs import run_brief

router = APIRouter(prefix="/v1", tags=["fleet"], dependencies=[Admin])


#: Which metric best summarizes an agent's quality, depending on what its suites measure.
PRIMARY_QUALITY = (
    "quality.correctness",
    "quality.field_accuracy",
    "quality.tool_use",
    "factuality.faithfulness",
    "quality.contains",
)


def primary_quality(metrics: dict[str, object] | None) -> tuple[str | None, float | None]:
    for name in PRIMARY_QUALITY:
        value = (metrics or {}).get(name)
        if isinstance(value, int | float):
            return name, float(value)
    return None, None


async def redteam_asr(session: Session) -> dict[str, float]:
    """Attack success rate per agent from its most recent red-team campaign (full security suites)."""
    rows = (
        await session.execute(
            select(db.Run.agent, db.Run.metrics)
            .distinct(db.Run.agent)
            .where(db.Run.kind == "redteam")
            .order_by(db.Run.agent, db.Run.created_at.desc())
        )
    ).all()
    return {
        a: float(m["safety.attack_success_rate"])
        for a, m in rows
        if (m or {}).get("safety.attack_success_rate") is not None
    }


def agent_out(a: db.Agent) -> AgentOut:
    return AgentOut(
        slug=a.slug,
        name=a.name,
        team=a.team,
        runtime=a.runtime,
        model=a.model,
        description=a.description,
        suites=a.suites,
    )


TRAFFIC_SQL = text(
    """
    select agent,
           count(*) filter (where operation = 'invoke_agent') as requests,
           count(*) filter (where operation = 'invoke_agent' and status = 'error') as errors,
           percentile_cont(0.95) within group (order by duration_ms) filter (where operation = 'invoke_agent') as p95,
           coalesce(sum(cost_usd) filter (where operation = 'chat'), 0) as cost
    from spans
    where source = 'live' and agent is not null and start_time > now() - make_interval(hours => :hours)
    group by agent
    """
)


async def traffic_by_agent(session: Session, hours: int = 24) -> dict[str, Traffic]:
    rows = (await session.execute(TRAFFIC_SQL, {"hours": hours})).all()
    out = {}
    for agent, requests, errors, p95, cost in rows:
        out[agent] = Traffic(
            requests=int(requests),
            errors=int(errors),
            error_rate=(errors / requests) if requests else 0.0,
            p95_ms=float(p95) if p95 is not None else None,
            cost_usd=float(cost),
        )
    return out


def classify(
    run: db.Run | None,
    gate: db.GateEvaluation | None,
    traffic: Traffic,
    online: dict[str, float],
    asr: float | None = None,
) -> tuple[str, list[str]]:
    """Agent health from the latest evaluation, last gate, live error rate and online scores."""
    reasons: list[str] = []
    failing = warning = False
    if run is None:
        return "unknown", ["no evaluation yet"]
    m = run.metrics or {}
    if gate is not None and gate.verdict == "FAIL":
        failing = True
        reasons.append("last quality gate failed")
    # Security health comes from the full red-team campaign; the nightly sample is too small to judge on.
    asr = asr if asr is not None else m.get("safety.attack_success_rate")
    if (asr or 0) > 0.10:
        failing = True
        reasons.append(f"attack success rate {asr:.0%}")
    elif (asr or 0) > 0.05:
        warning = True
        reasons.append(f"attack success rate {asr:.0%}")
    if (m.get("factuality.hallucination_rate") or 0) > 0.12:
        warning = True
        reasons.append(f"hallucination rate {m['factuality.hallucination_rate']:.0%}")
    if traffic.requests >= 20 and traffic.error_rate > 0.05:
        failing = True
        reasons.append(f"live error rate {traffic.error_rate:.0%}")
    if gate is not None and gate.verdict == "WARN":
        warning = True
        reasons.append("last gate passed with warnings")
    if online.get("factuality.faithfulness", 1.0) < 0.8:
        warning = True
        reasons.append("online faithfulness below 80%")
    return ("failing" if failing else "warning" if warning else "healthy"), reasons


async def online_scores(session: Session) -> dict[str, dict[str, float]]:
    rows = (
        await session.execute(
            text(
                "select agent, metric, avg(value) from online_scores where created_at > now() - interval '24 hours' and value is not null group by agent, metric"
            )
        )
    ).all()
    out: dict[str, dict[str, float]] = {}
    for agent, metric, value in rows:
        out.setdefault(agent, {})[metric] = float(value)
    return out


@router.get("/overview", response_model=Overview)
async def overview(session: Session) -> Overview:
    agents = (await session.execute(select(db.Agent).order_by(db.Agent.slug))).scalars().all()
    latest_runs = {
        r.agent: r
        for r in (
            await session.execute(
                select(db.Run)
                .distinct(db.Run.agent)
                .where(db.Run.kind == "eval")
                .order_by(db.Run.agent, db.Run.created_at.desc())
            )
        ).scalars()
    }
    latest_gates = {
        g.agent: g
        for g in (
            await session.execute(
                select(db.GateEvaluation)
                .distinct(db.GateEvaluation.agent)
                .order_by(db.GateEvaluation.agent, db.GateEvaluation.created_at.desc())
            )
        ).scalars()
    }
    traffic = await traffic_by_agent(session)
    online = await online_scores(session)

    asr_by_agent = await redteam_asr(session)
    since = datetime.now(UTC) - timedelta(days=14)
    trend_rows = (
        await session.execute(
            select(db.Run.agent, db.Run.metrics)
            .where(db.Run.kind == "eval", db.Run.created_at > since)
            .order_by(db.Run.created_at)
        )
    ).all()
    series: dict[str, list[dict[str, object]]] = {}
    for agent, metrics in trend_rows:
        series.setdefault(agent, []).append(metrics or {})

    health: list[AgentHealth] = []
    for a in agents:
        run, gate = latest_runs.get(a.slug), latest_gates.get(a.slug)
        t = traffic.get(a.slug, Traffic())
        asr = asr_by_agent.get(a.slug)
        status, reasons = classify(run, gate, t, online.get(a.slug, {}), asr)
        metric, quality = primary_quality(run.metrics if run else None)
        trend: list[float] = []
        for m in series.get(a.slug, []):
            value = m.get(metric) if metric else None
            if isinstance(value, int | float):
                trend.append(float(value))
        trend = trend[-14:]
        health.append(
            AgentHealth(
                agent=agent_out(a),
                status=status,
                reasons=reasons,
                latest_run=run_brief(run) if run else None,
                gate=gate_brief(gate) if gate else None,
                traffic=t,
                quality=quality,
                quality_metric=metric,
                asr=asr
                if asr is not None
                else ((run.metrics or {}).get("safety.attack_success_rate") if run else None),
                trend=trend,
                online=online.get(a.slug, {}),
            )
        )
    counts = {
        s: sum(1 for h in health if h.status == s) for s in ("healthy", "warning", "failing", "unknown")
    }
    reqs = sum(h.traffic.requests for h in health)
    errs = sum(h.traffic.errors for h in health)
    week = datetime.now(UTC) - timedelta(days=7)
    runs_7d = (
        await session.execute(text("select count(*) from runs where created_at > :t"), {"t": week})
    ).scalar_one()
    gate_fail_7d = (
        await session.execute(
            text("select count(*) from gate_evaluations where verdict = 'FAIL' and created_at > :t"),
            {"t": week},
        )
    ).scalar_one()
    recent = (
        (
            await session.execute(
                select(db.GateEvaluation).order_by(db.GateEvaluation.created_at.desc()).limit(8)
            )
        )
        .scalars()
        .all()
    )
    return Overview(
        generated_at=datetime.now(UTC),
        totals=OverviewTotals(
            agents=len(health),
            requests_24h=reqs,
            cost_24h=sum(h.traffic.cost_usd for h in health),
            error_rate_24h=(errs / reqs) if reqs else 0.0,
            runs_7d=int(runs_7d),
            gate_failures_7d=int(gate_fail_7d),
            **counts,
        ),
        agents=health,
        recent_gates=[gate_brief(g) for g in recent],
    )


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(session: Session) -> list[AgentOut]:
    return [agent_out(a) for a in (await session.execute(select(db.Agent).order_by(db.Agent.slug))).scalars()]


@router.get("/agents/{slug}", response_model=AgentHealth)
async def get_agent(slug: str, session: Session) -> AgentHealth:
    agent = (await session.execute(select(db.Agent).where(db.Agent.slug == slug))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    run = (
        await session.execute(
            select(db.Run)
            .where(db.Run.agent == slug, db.Run.kind == "eval")
            .order_by(db.Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    gate = (
        await session.execute(
            select(db.GateEvaluation)
            .where(db.GateEvaluation.agent == slug)
            .order_by(db.GateEvaluation.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    traffic = (await traffic_by_agent(session)).get(slug, Traffic())
    online = (await online_scores(session)).get(slug, {})
    asr = (await redteam_asr(session)).get(slug)
    status, reasons = classify(run, gate, traffic, online, asr)
    metric, quality = primary_quality(run.metrics if run else None)
    return AgentHealth(
        quality=quality,
        quality_metric=metric,
        asr=asr,
        agent=agent_out(agent),
        status=status,
        reasons=reasons,
        latest_run=run_brief(run) if run else None,
        gate=gate_brief(gate) if gate else None,
        traffic=traffic,
        online=online,
    )


@router.get("/agents/{slug}/trend", response_model=list[TrendPoint])
async def agent_trend(
    slug: str,
    session: Session,
    metric: str = "quality.correctness",
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    kind: str = "eval",
) -> list[TrendPoint]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(db.Run.id, db.Run.created_at, db.Run.model, db.Run.metrics)
            .where(db.Run.agent == slug, db.Run.kind == kind, db.Run.created_at > since)
            .order_by(db.Run.created_at)
        )
    ).all()
    return [TrendPoint(run_id=i, created_at=c, model=m, value=(mt or {}).get(metric)) for i, c, m, mt in rows]


BUCKET_SQL = text(
    """
    select date_bin(make_interval(mins => :bucket_min), start_time, timestamptz '2000-01-01') as ts,
           count(*) filter (where operation = 'invoke_agent') as requests,
           count(*) filter (where operation = 'invoke_agent' and status = 'error') as errors,
           percentile_cont(0.5) within group (order by duration_ms) filter (where operation = 'invoke_agent') as p50,
           percentile_cont(0.95) within group (order by duration_ms) filter (where operation = 'invoke_agent') as p95,
           coalesce(sum(input_tokens) filter (where operation = 'chat'), 0) as tin,
           coalesce(sum(output_tokens) filter (where operation = 'chat'), 0) as tout,
           coalesce(sum(cost_usd) filter (where operation = 'chat'), 0) as cost
    from spans
    where source = 'live' and (cast(:agent as text) is null or agent = :agent) and start_time > now() - make_interval(hours => :hours)
    group by 1 order by 1
    """
)


async def traffic_series(
    session: Session, agent: str | None, hours: int, bucket_min: int
) -> list[TrafficBucket]:
    rows = (
        await session.execute(BUCKET_SQL, {"agent": agent, "hours": hours, "bucket_min": bucket_min})
    ).all()
    return [
        TrafficBucket(
            ts=ts,
            requests=int(r),
            errors=int(e),
            p50_ms=float(p50) if p50 is not None else None,
            p95_ms=float(p95) if p95 is not None else None,
            tokens_in=int(tin),
            tokens_out=int(tout),
            cost_usd=float(c),
        )
        for ts, r, e, p50, p95, tin, tout, c in rows
    ]


@router.get("/agents/{slug}/traffic", response_model=list[TrafficBucket])
async def agent_traffic(
    slug: str,
    session: Session,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    bucket_min: Annotated[int, Query(ge=1, le=1440)] = 60,
) -> list[TrafficBucket]:
    return await traffic_series(session, slug, hours, bucket_min)


@router.get("/traffic", response_model=list[TrafficBucket])
async def fleet_traffic(
    session: Session,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    bucket_min: Annotated[int, Query(ge=1, le=1440)] = 60,
) -> list[TrafficBucket]:
    return await traffic_series(session, None, hours, bucket_min)


@router.get("/agents/{slug}/runs", response_model=list[RunBrief])
async def agent_runs(
    slug: str, session: Session, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[RunBrief]:
    rows = (
        (
            await session.execute(
                select(db.Run).where(db.Run.agent == slug).order_by(db.Run.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [run_brief(r) for r in rows]
