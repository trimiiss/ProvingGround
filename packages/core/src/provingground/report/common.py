"""Shared helpers for rendering runs, comparisons and gate results."""

from __future__ import annotations

from ..metrics import format_metric
from ..models import CaseResult, MetricStat, Run

# Metrics worth surfacing at the top of a report, in order, with friendly labels.
HEADLINE_METRICS: list[tuple[str, str]] = [
    ("quality.correctness", "Correctness"),
    ("factuality.faithfulness", "Faithfulness"),
    ("factuality.hallucination_rate", "Hallucination rate"),
    ("quality.field_accuracy", "Field accuracy"),
    ("quality.token_f1", "Token F1"),
    ("quality.relevance", "Relevance"),
    ("quality.tool_use", "Tool use"),
    ("safety.attack_success_rate", "Attack success rate"),
    ("safety.false_refusal_rate", "False refusal rate"),
    ("perf.latency_p95_ms", "Latency p95"),
    ("perf.latency_mean_ms", "Latency mean"),
    ("cost.per_1k_requests_usd", "Cost / 1k requests"),
    ("reliability.error_rate", "Error rate"),
]


def metric_label(metric: str) -> str:
    for name, label in HEADLINE_METRICS:
        if name == metric:
            return label
    return metric


def ci_text(stat: MetricStat | None) -> str:
    if stat is None or stat.ci_low is None or stat.ci_high is None:
        return ""
    return f"[{format_metric(stat.metric, stat.ci_low)}, {format_metric(stat.metric, stat.ci_high)}]"


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def failing_results(run: Run, limit: int = 20) -> list[CaseResult]:
    scored = [r for r in run.results if r.passed is False]
    scored.sort(key=lambda r: (not r.critical, r.suite, r.case_id))
    return scored[:limit]


def projected_monthly_cost(run: Run, requests_per_month: int) -> float | None:
    summary = run.summary
    scorable = summary.n_results - sum(
        summary.status_counts.get(s, 0) for s in ("error", "timeout", "model_mismatch")
    )
    if not summary.total_cost_usd or scorable <= 0:
        return None
    return summary.total_cost_usd / scorable * requests_per_month
