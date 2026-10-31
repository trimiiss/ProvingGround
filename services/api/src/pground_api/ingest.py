"""Store OTLP spans and update Prometheus event metrics."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from provingground.models import SpanRecord

from . import metrics
from .db import models as db
from .otlp import resolve_agents, span_row


async def ingest_spans(session: AsyncSession, spans: list[SpanRecord]) -> int:
    """Insert spans idempotently. Returns how many were new."""
    if not spans:
        return 0
    agents = resolve_agents(spans)
    # Spans of one trace can arrive in different export batches: resolve from what is stored.
    unresolved = {s.trace_id for s in spans if s.span_id not in agents}
    if unresolved:
        rows = (
            await session.execute(
                select(db.Span.trace_id, db.Span.span_id, db.Span.agent).where(
                    db.Span.trace_id.in_(unresolved), db.Span.agent.is_not(None)
                )
            )
        ).all()
        known_by_trace: dict[str, dict[str, str]] = defaultdict(dict)
        for trace_id, span_id, known_agent in rows:
            if known_agent:
                known_by_trace[trace_id][span_id] = known_agent
        for trace_id in unresolved:
            batch = [s for s in spans if s.trace_id == trace_id]
            for span_id, agent in resolve_agents(batch, known_by_trace.get(trace_id)).items():
                agents.setdefault(span_id, agent)
        # Any agent seen in the trace applies to its remaining spans.
        for trace_id, known in known_by_trace.items():
            fallback = next(iter(known.values()))
            for s in spans:
                if s.trace_id == trace_id:
                    agents.setdefault(s.span_id, fallback)

    eval_traces = {s.trace_id for s in spans if "pground.run_id" in s.attributes}
    payload = [
        span_row(s, agent=agents.get(s.span_id), source="eval" if s.trace_id in eval_traces else "live")
        for s in spans
    ]
    stmt = (
        pg_insert(db.Span)
        .on_conflict_do_nothing(index_elements=["trace_id", "span_id"])
        .returning(db.Span.trace_id, db.Span.span_id)
    )
    inserted = {(t, i) for t, i in (await session.execute(stmt, payload)).all()} if payload else set()
    for row in payload:
        if (row["trace_id"], row["span_id"]) in inserted:
            metrics.observe_span(row)
            metrics.SPANS_INGESTED.labels(row["source"]).inc()
    return len(inserted)
