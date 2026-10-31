"""PostgreSQL schema (SQLAlchemy 2.0). Migrations live in ``services/api/migrations``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


def now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    team: Mapped[str] = mapped_column(String(100), default="")
    runtime: Mapped[str] = mapped_column(String(50), default="")
    model: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    suites: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    agent: Mapped[str] = mapped_column(String(100), index=True)  # target name (agent slug)
    model: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), default="eval")  # eval | redteam | inspect | online
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_results: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # metric -> mean (for fast trends)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    suites: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    scorer_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    git: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # TargetInfo
    judge_model: Mapped[str | None] = mapped_column(String(200))

    results: Mapped[list[CaseResultRow]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_runs_agent_created", "agent", "created_at"),)


class CaseResultRow(Base):
    __tablename__ = "case_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    suite: Mapped[str] = mapped_column(String(100))
    case_id: Mapped[str] = mapped_column(String(200))
    repeat: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    input_preview: Mapped[str] = mapped_column(Text, default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    trace_id: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)  # full CaseResult

    run: Mapped[Run] = relationship(back_populates="results")
    scores: Mapped[list[ScoreRow]] = relationship(back_populates="result", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_case_results_run_suite", "run_id", "suite"),
        Index("ix_case_results_run_passed", "run_id", "passed"),
    )


class ScoreRow(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("case_results.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(100))
    metric: Mapped[str] = mapped_column(String(120))
    value: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)

    result: Mapped[CaseResultRow] = relationship(back_populates="scores")

    __table_args__ = (Index("ix_scores_run_metric", "run_id", "metric"),)


class Comparison(Base):
    __tablename__ = "comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_run_id: Mapped[str] = mapped_column(String(100))
    candidate_run_id: Mapped[str] = mapped_column(String(100))
    agent: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = now_col()

    __table_args__ = (UniqueConstraint("baseline_run_id", "candidate_run_id"),)


class GatePolicyRow(Base):
    __tablename__ = "gate_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GateEvaluation(Base):
    __tablename__ = "gate_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy: Mapped[str] = mapped_column(String(100))
    agent: Mapped[str] = mapped_column(String(100), index=True)
    candidate_run_id: Mapped[str] = mapped_column(String(100))
    baseline_run_id: Mapped[str | None] = mapped_column(String(100))
    verdict: Mapped[str] = mapped_column(String(20))
    exit_code: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(300), default="")  # file path for repo datasets
    version: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = now_col()

    cases: Mapped[list[TestCaseRow]] = relationship(back_populates="dataset", cascade="all, delete-orphan")


class TestCaseRow(Base):
    __tablename__ = "test_cases"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = now_col()

    dataset: Mapped[Dataset] = relationship(back_populates="cases")

    __table_args__ = (UniqueConstraint("dataset_id", "case_id"),)


class Span(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="internal")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="unset")
    service: Mapped[str | None] = mapped_column(String(100))
    agent: Mapped[str | None] = mapped_column(String(100))
    operation: Mapped[str | None] = mapped_column(
        String(40)
    )  # chat | execute_tool | invoke_agent | retrieval
    model: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="live")  # live | eval
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    events: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    ingested_at: Mapped[datetime] = now_col()

    __table_args__ = (
        UniqueConstraint("trace_id", "span_id"),
        Index("ix_spans_agent_start", "agent", "start_time"),
        Index("ix_spans_start", "start_time"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(30))  # run | gate | redteam | online_eval
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[float | None] = mapped_column(Float)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    schedule_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_claim", "status", "run_after", "priority"),)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    cron: Mapped[str] = mapped_column(String(100))
    job_kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnlineScore(Base):
    """Reference-free scores of sampled live traces (Phase 6 online evaluation)."""

    __tablename__ = "online_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(100))
    metric: Mapped[str] = mapped_column(String(120))
    value: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_online_agent_metric_time", "agent", "metric", "created_at"),)
