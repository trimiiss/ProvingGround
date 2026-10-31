"""Core data contracts shared by the engine, the platform API and the dashboard.

Everything that crosses a boundary (target -> runner -> scorer -> store -> API) is one of
these pydantic models, so a run written by the CLI can be loaded by the server unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class CaseStatus(StrEnum):
    """Outcome of invoking a target on one case.

    Only ``ok`` and ``refused`` results are scored. Everything else is an infrastructure or
    harness outcome and is reported under ``reliability.*``; it is never scored as a wrong
    answer ("no answer" is not a "negative answer").
    """

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    MODEL_MISMATCH = "model_mismatch"


SCORABLE_STATUSES = frozenset({CaseStatus.OK, CaseStatus.REFUSED})


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: Any) -> str:
    text = data if isinstance(data, str) else canonical_json(data)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Provider-native content blocks (e.g. Anthropic thinking + tool_use blocks). They are
    # echoed back verbatim on the next request of a tool loop.
    provider_content: list[dict[str, Any]] | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class Usage(BaseModel):
    """Token usage. ``input_tokens`` counts uncached input only; cache reads/writes are separate."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class TestCase(BaseModel):
    """One evaluation case. Exactly one of ``input`` or ``messages`` must be set."""

    __test__ = False  # keep pytest from collecting this class

    model_config = ConfigDict(extra="forbid")

    id: str
    input: str | None = None
    messages: list[Message] | None = None
    expected: str | None = None
    context: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    critical: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_prompt(self) -> TestCase:
        if (self.input is None) == (self.messages is None):
            raise ValueError(f"case {self.id!r}: set exactly one of 'input' or 'messages'")
        return self

    @property
    def prompt_text(self) -> str:
        """The latest user utterance (used for previews and by simple scorers)."""
        if self.input is not None:
            return self.input
        for message in reversed(self.messages or []):
            if message.role == "user":
                return message.content
        return ""

    def conversation(self) -> list[Message]:
        if self.messages is not None:
            return list(self.messages)
        return [Message(role="user", content=self.input or "")]

    def content_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


class SpanRecord(BaseModel):
    """A finished OpenTelemetry span in a storage-friendly shape."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str = "internal"
    start_time_ns: int
    end_time_ns: int
    status: str = "unset"
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    service_name: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time_ns - self.start_time_ns) / 1e6


class TargetOutput(BaseModel):
    status: CaseStatus = CaseStatus.OK
    text: str = ""
    messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    retrieved_context: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model_requested: str | None = None
    model_served: str | None = None
    stop_reason: str | None = None
    latency_ms: float | None = None  # final successful attempt only
    wall_ms: float | None = None  # includes retries and backoff (not semaphore queueing)
    cost_usd: float | None = None  # None means the price is unknown, never "free"
    attempts: int = 1
    error: str | None = None
    trace_id: str | None = None
    spans: list[SpanRecord] = Field(default_factory=list)
    cached: bool = False
    # Target-specific extras. ``raw["sim"]`` carries simulator ground truth (injected faults);
    # scorers must not read it - only `pground doctor` uses it to validate detectors.
    raw: dict[str, Any] = Field(default_factory=dict)


class ToolPolicy(BaseModel):
    """A rule describing a tool call the target must never make."""

    tool: str
    description: str = ""
    arg: str | None = None
    op: Literal[
        "always",
        "gt",
        "gte",
        "lt",
        "lte",
        "eq",
        "neq",
        "in",
        "not_in",
        "endswith_any",
        "not_endswith_any",
        "contains",
        "not_contains",
    ] = "always"
    value: Any = None

    def violated_by(self, call: ToolCall) -> bool:
        if call.name != self.tool:
            return False
        if self.op == "always":
            return True
        if self.arg is None or self.arg not in call.arguments:
            return False
        actual = call.arguments[self.arg]
        try:
            match self.op:
                case "gt":
                    return float(actual) > float(self.value)
                case "gte":
                    return float(actual) >= float(self.value)
                case "lt":
                    return float(actual) < float(self.value)
                case "lte":
                    return float(actual) <= float(self.value)
                case "eq":
                    return bool(actual == self.value)
                case "neq":
                    return bool(actual != self.value)
                case "in":
                    return actual in (self.value or [])
                case "not_in":
                    return actual not in (self.value or [])
                case "endswith_any":
                    return any(str(actual).lower().endswith(str(v).lower()) for v in self.value or [])
                case "not_endswith_any":
                    return not any(str(actual).lower().endswith(str(v).lower()) for v in self.value or [])
                case "contains":
                    return str(self.value).lower() in str(actual).lower()
                case "not_contains":
                    return str(self.value).lower() not in str(actual).lower()
        except (TypeError, ValueError):
            return False
        return False


class TargetInfo(BaseModel):
    name: str
    kind: str
    model: str | None = None
    fingerprint: str
    description: str = ""
    canaries: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    tool_policies: list[ToolPolicy] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    cacheable: bool = True


class Score(BaseModel):
    metric: str
    value: float | None  # None: not applicable / could not be scored
    passed: bool | None = None
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    scorer: str = ""
    scorer_version: str = "1"
    judge_model: str | None = None
    judge_usage: Usage | None = None
    judge_cost_usd: float | None = None


class CaseResult(BaseModel):
    suite: str
    case_id: str
    repeat: int = 0
    tags: list[str] = Field(default_factory=list)
    critical: bool = False
    input_preview: str = ""
    expected: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: CaseStatus
    output: TargetOutput
    scores: list[Score] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool | None:
        """True when every pass/fail score passed; None when not scorable or nothing to judge."""
        if self.status not in SCORABLE_STATUSES:
            return None
        verdicts = [s.passed for s in self.scores if s.passed is not None]
        return all(verdicts) if verdicts else None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.suite, self.case_id, self.repeat)

    def score(self, metric: str) -> Score | None:
        for s in self.scores:
            if s.metric == metric:
                return s
        return None


class MetricStat(BaseModel):
    metric: str
    n: int
    mean: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    pass_rate: float | None = None
    direction: Literal["higher", "lower"] = "higher"
    kind: Literal["case", "run"] = "case"


class RunSummary(BaseModel):
    n_cases: int = 0
    n_results: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    pass_rate: float | None = None
    metrics: dict[str, MetricStat] = Field(default_factory=dict)
    by_suite: dict[str, dict[str, MetricStat]] = Field(default_factory=dict)
    total_cost_usd: float | None = None
    judge_cost_usd: float = 0.0
    cost_unknown_results: int = 0


class SuiteInfo(BaseModel):
    name: str
    dataset: str | None = None
    dataset_version: str
    n_cases: int
    scorers: list[str] = Field(default_factory=list)


class GitInfo(BaseModel):
    sha: str | None = None
    ref: str | None = None
    dirty: bool | None = None


class Run(BaseModel):
    schema_version: int = 1
    id: str
    created_at: datetime
    finished_at: datetime | None = None
    project: str | None = None
    target: TargetInfo
    suites: list[SuiteInfo] = Field(default_factory=list)
    scorer_versions: dict[str, str] = Field(default_factory=dict)
    judge_model: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    git: GitInfo | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    results: list[CaseResult] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)

    def results_by_key(self) -> dict[tuple[str, str, int], CaseResult]:
        return {r.key: r for r in self.results}

    def suite_versions(self) -> dict[str, str]:
        return {s.name: s.dataset_version for s in self.suites}
