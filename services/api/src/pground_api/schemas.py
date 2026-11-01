"""Response / request models (the OpenAPI schema the dashboard's TypeScript client is generated from)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Health = Literal["healthy", "warning", "failing", "unknown"]


class AgentOut(BaseModel):
    slug: str
    name: str
    team: str = ""
    runtime: str = ""
    model: str | None = None
    description: str = ""
    suites: list[str] = Field(default_factory=list)


class RunBrief(BaseModel):
    id: str
    agent: str
    model: str | None = None
    kind: str
    created_at: datetime
    n_results: int
    pass_rate: float | None = None
    cost_usd: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)


class RunDetail(RunBrief):
    summary: dict[str, Any] = Field(default_factory=dict)
    suites: list[dict[str, Any]] = Field(default_factory=list)
    scorer_versions: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    git: dict[str, Any] | None = None
    target: dict[str, Any] = Field(default_factory=dict)
    judge_model: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int


class ResultBrief(BaseModel):
    id: int
    run_id: str
    suite: str
    case_id: str
    repeat: int
    status: str
    passed: bool | None
    critical: bool
    tags: list[str] = Field(default_factory=list)
    input_preview: str = ""
    output_text: str = ""
    latency_ms: float | None = None
    cost_usd: float | None = None
    trace_id: str | None = None


class ResultDetail(ResultBrief):
    payload: dict[str, Any]


class GateBrief(BaseModel):
    id: int
    policy: str
    agent: str
    verdict: str
    candidate_run_id: str
    baseline_run_id: str | None = None
    created_at: datetime


class GateEvaluationOut(GateBrief):
    result: dict[str, Any]


class Traffic(BaseModel):
    requests: int = 0
    errors: int = 0
    error_rate: float = 0.0
    p95_ms: float | None = None
    cost_usd: float = 0.0


class AgentHealth(BaseModel):
    agent: AgentOut
    status: Health
    reasons: list[str] = Field(default_factory=list)
    latest_run: RunBrief | None = None
    gate: GateBrief | None = None
    traffic: Traffic = Field(default_factory=Traffic)
    quality: float | None = None  # latest value of the agent's primary quality metric
    quality_metric: str | None = None
    asr: float | None = None  # attack success rate from the latest red-team campaign
    trend: list[float] = Field(default_factory=list)  # recent values of the primary quality metric
    online: dict[str, float] = Field(default_factory=dict)


class OverviewTotals(BaseModel):
    agents: int
    healthy: int
    warning: int
    failing: int
    unknown: int
    requests_24h: int
    cost_24h: float
    error_rate_24h: float
    runs_7d: int
    gate_failures_7d: int


class Overview(BaseModel):
    generated_at: datetime
    totals: OverviewTotals
    agents: list[AgentHealth]
    recent_gates: list[GateBrief]


class TrendPoint(BaseModel):
    run_id: str
    created_at: datetime
    model: str | None = None
    value: float | None = None


class TrafficBucket(BaseModel):
    ts: datetime
    requests: int
    errors: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class SpanOut(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str
    start_time: datetime
    duration_ms: float
    status: str
    service: str | None = None
    agent: str | None = None
    operation: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    source: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[Any] = Field(default_factory=list)


class TraceBrief(BaseModel):
    trace_id: str
    agent: str | None = None
    started_at: datetime
    duration_ms: float | None = None
    has_error: bool = False
    model: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    n_spans: int
    source: str
    input: str | None = None
    output: str | None = None


class TraceDetail(BaseModel):
    trace: TraceBrief
    spans: list[SpanOut]
    online_scores: list[dict[str, Any]] = Field(default_factory=list)


class JobIn(BaseModel):
    kind: Literal["run", "redteam", "gate", "online_eval"]
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    payload: dict[str, Any]
    progress: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DatasetOut(BaseModel):
    name: str
    description: str = ""
    source: str = ""
    version: str = ""
    n_cases: int


class CaseIn(BaseModel):
    case_id: str | None = None
    input: str | None = None
    messages: list[dict[str, Any]] | None = None
    expected: str | None = None
    context: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    critical: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceToDataset(BaseModel):
    dataset: str = "incidents"
    expected: str | None = None
    tags: list[str] = Field(default_factory=list)
    critical: bool = True


class ScheduleIn(BaseModel):
    name: str
    cron: str
    job_kind: Literal["run", "redteam", "gate", "online_eval"]
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduleOut(ScheduleIn):
    id: int
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class GateEvaluateIn(BaseModel):
    policy: str = "release"
    candidate_run_id: str
    baseline_run_id: str | None = None


class ModelStats(BaseModel):
    model: str
    agents: list[str]
    runs: int
    metrics: dict[str, float]
    cost_per_1k_usd: float | None = None
    traffic_requests: int = 0
    traffic_p95_ms: float | None = None
    on_pareto_front: bool = False


class ConfigOut(BaseModel):
    project: str
    targets: list[dict[str, Any]]
    suites: list[dict[str, Any]]
    gates: list[str]
    providers: list[str]
