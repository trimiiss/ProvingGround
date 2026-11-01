"""Online evaluation of live traffic: sample recent production traces and score them.

Runs as the ``online_eval`` job (scheduled every few minutes). Each trace is scored at most once,
and sampling is deterministic by trace id, so overlapping windows and concurrent workers never
double-count.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from provingground.config import ConfigError
from provingground.engine import build_one_target
from provingground.models import TargetInfo
from provingground.online import LiveRequest, sampled, score_request
from provingground.providers import ProviderRegistry
from provingground.scorers.judge import build_judge

from .db import models as db
from .deps import project_config


async def pending_requests(
    session: AsyncSession, *, hours: float, rate: float, limit: int
) -> list[LiveRequest]:
    """Sampled live agent invocations from the window that have not been scored yet."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    roots = (
        (
            await session.execute(
                select(db.Span)
                .where(
                    db.Span.source == "live",
                    db.Span.operation == "invoke_agent",
                    db.Span.start_time > since,
                    db.Span.status != "error",  # failures are already counted by the error rate
                    db.Span.attributes.has_key("pground.input"),  # content tracing may be off
                    ~exists().where(db.OnlineScore.trace_id == db.Span.trace_id),
                )
                .order_by(db.Span.start_time.desc())
                .limit(limit * 20)
            )
        )
        .scalars()
        .all()
    )
    picked = [r for r in roots if r.agent and sampled(r.trace_id, rate)][:limit]
    if not picked:
        return []
    docs_by_trace: dict[str, list[str]] = {}
    for span in (
        await session.execute(
            select(db.Span).where(
                db.Span.trace_id.in_([r.trace_id for r in picked]),
                db.Span.attributes.has_key("pground.retrieval.docs"),
            )
        )
    ).scalars():
        docs = span.attributes.get("pground.retrieval.docs")
        if docs:
            docs_by_trace[span.trace_id] = [str(d) for d in docs]
    return [
        LiveRequest(
            trace_id=r.trace_id,
            agent=r.agent or "",
            question=str(r.attributes.get("pground.input", "")),
            answer=str(r.attributes.get("pground.output", "")),
            docs=docs_by_trace.get(r.trace_id, []),
            error=r.status == "error",
        )
        for r in picked
    ]


async def run_online_eval(
    session: AsyncSession, *, hours: float = 1.0, rate: float = 0.2, limit: int = 300
) -> dict[str, Any]:
    cfg = project_config()
    registry = ProviderRegistry(cfg.providers)
    judge = build_judge(cfg.judge, registry)
    requests = await pending_requests(session, hours=hours, rate=rate, limit=limit)
    targets: dict[str, TargetInfo | None] = {}
    scored = rows = 0
    for req in requests:
        if req.agent not in targets:
            targets[req.agent] = None
            with contextlib.suppress(ConfigError):
                target = build_one_target(cfg, req.agent)
                targets[req.agent] = target.info
                await target.aclose()
        info = targets[req.agent]
        if info is None or not req.question:  # unknown agent, or content tracing is off
            continue
        scores = await score_request(req, info, judge)
        session.add_all(
            db.OnlineScore(
                trace_id=req.trace_id,
                agent=req.agent,
                metric=s.metric,
                value=s.value,
                passed=s.passed,
                detail={"reason": s.reason, "scorer": s.scorer, "judge": s.judge_model},
            )
            for s in scores
        )
        scored += 1
        rows += len(scores)
    return {"candidates": len(requests), "scored": scored, "scores": rows, "rate": rate}
