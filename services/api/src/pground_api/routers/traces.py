"""OTLP trace ingest and trace exploration."""

from __future__ import annotations

import gzip
import zlib
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select, text

from provingground.models import TestCase

from .. import ingest, otlp
from ..db import models as db
from ..deps import Admin, Ingest, Session
from ..schemas import SpanOut, TraceBrief, TraceDetail, TraceToDataset
from .data import dataset_row

ingest_router = APIRouter(prefix="/v1", tags=["ingest"], dependencies=[Ingest])
router = APIRouter(prefix="/v1", tags=["traces"], dependencies=[Admin])


@ingest_router.post("/traces")
async def otlp_traces(request: Request, session: Session) -> Response:
    """OpenTelemetry OTLP/HTTP receiver (protobuf or JSON, optionally gzip/deflate)."""
    body = await request.body()
    encoding = request.headers.get("content-encoding", "").lower()
    try:
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            body = zlib.decompress(body)
        spans = otlp.parse_export(body, request.headers.get("content-type", "application/x-protobuf"))
    except Exception as exc:
        raise HTTPException(400, f"could not decode OTLP payload: {exc}") from exc
    await ingest.ingest_spans(session, spans)
    # An empty ExportTraceServiceResponse is a valid success body for both encodings.
    ctype = (
        "application/json" if "json" in request.headers.get("content-type", "") else "application/x-protobuf"
    )
    return Response(b"{}" if ctype == "application/json" else b"", media_type=ctype)


def span_out(s: db.Span) -> SpanOut:
    return SpanOut(
        trace_id=s.trace_id,
        span_id=s.span_id,
        parent_span_id=s.parent_span_id,
        name=s.name,
        kind=s.kind,
        start_time=s.start_time,
        duration_ms=s.duration_ms,
        status=s.status,
        service=s.service,
        agent=s.agent,
        operation=s.operation,
        model=s.model,
        input_tokens=s.input_tokens,
        output_tokens=s.output_tokens,
        cost_usd=s.cost_usd,
        source=s.source,
        attributes=s.attributes,
        events=s.events,
    )


TRACE_COLUMNS = """
    trace_id,
    min(start_time) as started_at,
    max(agent) as agent,
    max(duration_ms) filter (where operation = 'invoke_agent') as duration_ms,
    bool_or(status = 'error') as has_error,
    max(model) filter (where operation = 'chat') as model,
    sum(cost_usd) filter (where operation = 'chat') as cost,
    sum(input_tokens) filter (where operation = 'chat') as tin,
    sum(output_tokens) filter (where operation = 'chat') as tout,
    count(*) as n_spans,
    max(source) as source,
    max(attributes->>'pground.input') filter (where operation = 'invoke_agent') as input,
    max(attributes->>'pground.output') filter (where operation = 'invoke_agent') as output
"""

TRACE_LIST_SQL = f"""
    select {TRACE_COLUMNS}
    from spans
    where start_time > now() - make_interval(hours => :hours)
      and (cast(:agent as text) is null or agent = :agent)
      and (cast(:source as text) is null or source = :source)
    group by trace_id
    having (cast(:errors as boolean) is not true or bool_or(status = 'error'))
       and (cast(:q as text) is null
            or max(coalesce(attributes->>'pground.input', '') || ' ' || coalesce(attributes->>'pground.output', '')) ilike :like)
    order by min(start_time) desc
    limit :limit offset :offset
"""

TRACE_ONE_SQL = f"select {TRACE_COLUMNS} from spans where trace_id = :trace_id group by trace_id"


def trace_brief(row: Any) -> TraceBrief:
    return TraceBrief(
        trace_id=row.trace_id,
        agent=row.agent,
        started_at=row.started_at,
        duration_ms=float(row.duration_ms) if row.duration_ms is not None else None,
        has_error=bool(row.has_error),
        model=row.model,
        cost_usd=float(row.cost) if row.cost is not None else None,
        input_tokens=int(row.tin) if row.tin is not None else None,
        output_tokens=int(row.tout) if row.tout is not None else None,
        n_spans=int(row.n_spans),
        source=row.source,
        input=row.input,
        output=row.output,
    )


@router.get("/traces", response_model=list[TraceBrief])
async def list_traces(
    session: Session,
    agent: str | None = None,
    source: Annotated[str | None, Query(pattern="^(live|eval)$")] = None,
    errors_only: bool = False,
    q: str | None = None,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TraceBrief]:
    params = {
        "hours": hours,
        "agent": agent,
        "source": source,
        "errors": errors_only or None,
        "q": q,
        "like": f"%{q}%" if q else None,
        "limit": limit,
        "offset": offset,
    }
    rows = (await session.execute(text(TRACE_LIST_SQL), params)).all()
    return [trace_brief(r) for r in rows]


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str, session: Session) -> TraceDetail:
    spans = (
        (
            await session.execute(
                select(db.Span).where(db.Span.trace_id == trace_id).order_by(db.Span.start_time)
            )
        )
        .scalars()
        .all()
    )
    if not spans:
        raise HTTPException(404, "trace not found")
    row = (await session.execute(text(TRACE_ONE_SQL), {"trace_id": trace_id})).first()
    assert row is not None
    online = (
        await session.execute(
            text(
                "select metric, value, passed, detail, created_at from online_scores where trace_id = :t order by created_at"
            ),
            {"t": trace_id},
        )
    ).all()
    return TraceDetail(
        trace=trace_brief(row),
        spans=[span_out(s) for s in spans],
        online_scores=[
            {"metric": m, "value": v, "passed": p, "detail": d, "created_at": c.isoformat()}
            for m, v, p, d, c in online
        ],
    )


@router.post("/traces/{trace_id}/to-dataset", status_code=201)
async def trace_to_dataset(trace_id: str, body: TraceToDataset, session: Session) -> dict[str, Any]:
    """Turn a production trace into a regression case ("add to dataset")."""
    spans = (await session.execute(select(db.Span).where(db.Span.trace_id == trace_id))).scalars().all()
    root = next((s for s in spans if s.operation == "invoke_agent"), None)
    if root is None:
        raise HTTPException(422, "trace has no agent invocation to convert")
    user_input = root.attributes.get("pground.input")
    if not user_input:
        raise HTTPException(422, "trace did not record the user input (content tracing is disabled)")
    docs = next(
        (
            s.attributes.get("pground.retrieval.docs")
            for s in spans
            if s.attributes.get("pground.retrieval.docs")
        ),
        None,
    )
    case = TestCase(
        id=f"incident-{trace_id[:8]}",
        input=str(user_input),
        expected=body.expected,
        context=list(docs) if docs else None,
        tags=["incident", root.agent or "unknown", *body.tags],
        critical=body.critical,
        metadata={
            "source_trace": trace_id,
            "agent": root.agent,
            "observed_output": str(root.attributes.get("pground.output", ""))[:500],
        },
    )
    ds = await dataset_row(session, body.dataset, create=True)
    dup = (
        await session.execute(
            select(db.TestCaseRow).where(
                db.TestCaseRow.dataset_id == ds.id, db.TestCaseRow.case_id == case.id
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(409, f"{case.id} is already in {body.dataset!r}")
    session.add(
        db.TestCaseRow(
            dataset_id=ds.id, case_id=case.id, payload=case.model_dump(mode="json", exclude_defaults=True)
        )
    )
    return case.model_dump(mode="json", exclude_defaults=True)
