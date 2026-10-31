"""Persistence for evaluation runs: ProvingGround ``Run`` <-> PostgreSQL rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from provingground.models import CaseResult, Run, RunSummary, SuiteInfo, TargetInfo
from provingground.models import Run as RunModel

from .db import models as db
from .otlp import span_row

CHUNK = 2000


def run_kind(run: RunModel) -> str:
    labels = run.labels
    if labels.get("source") == "inspect":
        return "inspect"
    return labels.get("kind") or "eval"


def _metric_means(run: RunModel) -> dict[str, Any]:
    return {m: st.mean for m, st in run.summary.metrics.items() if st.mean is not None}


async def save_run(session: AsyncSession, run: RunModel, *, store_spans: bool = True) -> None:
    """Insert (or replace) a run with its case results, scores and eval spans."""
    await session.execute(delete(db.Run).where(db.Run.id == run.id))
    session.add(
        db.Run(
            id=run.id,
            agent=run.target.name,
            model=run.target.model,
            kind=run_kind(run),
            created_at=run.created_at,
            finished_at=run.finished_at,
            n_results=run.summary.n_results,
            pass_rate=run.summary.pass_rate,
            cost_usd=run.summary.total_cost_usd,
            metrics=_metric_means(run),
            summary=run.summary.model_dump(mode="json"),
            suites=[s.model_dump(mode="json") for s in run.suites],
            scorer_versions=run.scorer_versions,
            labels=run.labels,
            settings=run.settings,
            git=run.git.model_dump(mode="json") if run.git else None,
            target=run.target.model_dump(mode="json"),
            judge_model=run.judge_model,
        )
    )
    await session.flush()
    if not run.results:
        return

    n = len(run.results)
    ids = (
        (
            await session.execute(
                text("select nextval('case_results_id_seq') from generate_series(1, :n)"), {"n": n}
            )
        )
        .scalars()
        .all()
    )
    result_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    span_rows: list[dict[str, Any]] = []
    seen_spans: set[tuple[str, str]] = set()
    for rid, r in zip(ids, run.results, strict=True):
        spans = r.output.spans
        payload = r.model_dump(mode="json")
        payload["output"]["spans"] = []  # spans live in their own table
        result_rows.append(
            {
                "id": rid,
                "run_id": run.id,
                "suite": r.suite,
                "case_id": r.case_id,
                "repeat": r.repeat,
                "status": str(r.status),
                "passed": r.passed,
                "critical": r.critical,
                "tags": r.tags,
                "input_preview": r.input_preview,
                "output_text": r.output.text[:4000],
                "latency_ms": r.output.latency_ms,
                "cost_usd": r.output.cost_usd,
                "trace_id": r.output.trace_id,
                "payload": payload,
            }
        )
        for s in r.scores:
            score_rows.append(
                {"result_id": rid, "run_id": run.id, "metric": s.metric, "value": s.value, "passed": s.passed}
            )
        if store_spans:
            for sp in spans:
                key = (sp.trace_id, sp.span_id)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                span_rows.append(span_row(sp, agent=run.target.name, source="eval"))
    for start in range(0, len(result_rows), CHUNK):
        await session.execute(insert(db.CaseResultRow), result_rows[start : start + CHUNK])
    for start in range(0, len(score_rows), CHUNK * 3):
        await session.execute(insert(db.ScoreRow), score_rows[start : start + CHUNK * 3])
    for start in range(0, len(span_rows), CHUNK):
        stmt = pg_insert(db.Span).on_conflict_do_nothing(index_elements=["trace_id", "span_id"])
        await session.execute(stmt, span_rows[start : start + CHUNK])


def _run_from_row(row: db.Run, results: list[CaseResult]) -> RunModel:
    return Run(
        id=row.id,
        created_at=row.created_at,
        finished_at=row.finished_at,
        target=TargetInfo.model_validate(row.target),
        suites=[SuiteInfo.model_validate(s) for s in row.suites],
        scorer_versions=row.scorer_versions,
        judge_model=row.judge_model,
        settings=row.settings,
        labels=row.labels,
        summary=RunSummary.model_validate(row.summary),
        results=results,
    )


async def load_run(session: AsyncSession, run_id: str, *, with_results: bool = True) -> RunModel | None:
    row = await session.get(db.Run, run_id)
    if row is None:
        return None
    results: list[CaseResult] = []
    if with_results:
        rows = (
            await session.execute(
                select(db.CaseResultRow.payload)
                .where(db.CaseResultRow.run_id == run_id)
                .order_by(db.CaseResultRow.id)
            )
        ).scalars()
        results = [CaseResult.model_validate(p) for p in rows]
    return _run_from_row(row, results)


async def latest_run(
    session: AsyncSession,
    agent: str,
    *,
    kind: str = "eval",
    before: datetime | None = None,
    label: tuple[str, str] | None = None,
) -> db.Run | None:
    stmt = select(db.Run).where(db.Run.agent == agent, db.Run.kind == kind)
    if before:
        stmt = stmt.where(db.Run.created_at < before)
    if label:
        stmt = stmt.where(db.Run.labels[label[0]].astext == label[1])
    return (await session.execute(stmt.order_by(db.Run.created_at.desc()).limit(1))).scalar_one_or_none()


async def count_rows(session: AsyncSession, model: Any) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


def now() -> datetime:
    return datetime.now(UTC)
