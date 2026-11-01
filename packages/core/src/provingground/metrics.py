"""Metric naming conventions and run-level aggregation.

Metric names are ``<category>.<name>`` with categories quality, factuality, safety, perf,
cost and reliability. Direction is derived from the name so every consumer agrees on it:

* ``perf.*``, ``cost.*`` and ``reliability.*`` are lower-is-better;
* any metric ending in ``_rate`` counts bad events (hallucinations, attack successes,
  false refusals...) and is lower-is-better;
* everything else is a score in [0, 1] where higher is better.

Summaries are always recomputed from raw rows; nothing trusts a stored aggregate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

import numpy as np

from .models import SCORABLE_STATUSES, CaseResult, CaseStatus, MetricStat, RunSummary
from .stats import bootstrap_ci, percentile_stat

Direction = Literal["higher", "lower"]

LATENCY_PERCENTILES = (50, 90, 95, 99)


def direction(metric: str) -> Direction:
    if metric.startswith(("perf.", "cost.", "reliability.")) or metric.endswith("_rate"):
        return "lower"
    return "higher"


def is_run_level(metric: str) -> bool:
    return metric.startswith(("perf.", "cost.", "reliability."))


def latency_values(results: Iterable[CaseResult]) -> list[float]:
    return [
        r.output.latency_ms
        for r in results
        if r.status in SCORABLE_STATUSES and r.output.latency_ms is not None
    ]


def run_level_values(results: list[CaseResult]) -> dict[str, list[float]]:
    """Per-result values for run-level metrics (aligned rows where each metric applies)."""
    values: dict[str, list[float]] = {}
    lat = latency_values(results)
    if lat:
        values["perf.latency_ms"] = lat
    scorable = [r for r in results if r.status in SCORABLE_STATUSES]
    if scorable:
        values["perf.input_tokens_mean"] = [float(r.output.usage.total_input_tokens) for r in scorable]
        values["perf.output_tokens_mean"] = [float(r.output.usage.output_tokens) for r in scorable]
        costs = [r.output.cost_usd for r in scorable if r.output.cost_usd is not None]
        if costs:
            values["cost.per_1k_requests_usd"] = [c * 1000 for c in costs]
    if results:
        values["reliability.error_rate"] = [
            1.0 if r.status in (CaseStatus.ERROR, CaseStatus.TIMEOUT) else 0.0 for r in results
        ]
        values["reliability.timeout_rate"] = [1.0 if r.status == CaseStatus.TIMEOUT else 0.0 for r in results]
        values["reliability.truncation_rate"] = [
            1.0 if r.status == CaseStatus.TRUNCATED else 0.0 for r in results
        ]
        values["reliability.model_mismatch_rate"] = [
            1.0 if r.status == CaseStatus.MODEL_MISMATCH else 0.0 for r in results
        ]
    return values


def case_metric_values(results: Iterable[CaseResult]) -> dict[str, list[tuple[float, bool | None]]]:
    out: dict[str, list[tuple[float, bool | None]]] = defaultdict(list)
    for r in results:
        if r.status not in SCORABLE_STATUSES:
            continue
        for s in r.scores:
            if s.value is not None:
                out[s.metric].append((float(s.value), s.passed))
    return out


def _metric_stats(results: list[CaseResult], *, n_boot: int, seed: int) -> dict[str, MetricStat]:
    stats: dict[str, MetricStat] = {}
    for metric, pairs in sorted(case_metric_values(results).items()):
        vals = np.array([v for v, _ in pairs], dtype=float)
        verdicts = [p for _, p in pairs if p is not None]
        lo, hi = bootstrap_ci(vals, n_boot=n_boot, seed=seed)
        stats[metric] = MetricStat(
            metric=metric,
            n=len(vals),
            mean=float(vals.mean()),
            ci_low=lo,
            ci_high=hi,
            pass_rate=(sum(verdicts) / len(verdicts)) if verdicts else None,
            direction=direction(metric),
            kind="case",
        )
    run_vals = run_level_values(results)
    lat = run_vals.pop("perf.latency_ms", None)
    if lat:
        arr = np.array(lat, dtype=float)
        lo, hi = bootstrap_ci(arr, n_boot=n_boot, seed=seed)
        stats["perf.latency_mean_ms"] = MetricStat(
            metric="perf.latency_mean_ms", n=arr.size, mean=float(arr.mean()), ci_low=lo, ci_high=hi,
            direction="lower", kind="run",
        )  # fmt: skip
        for q in LATENCY_PERCENTILES:
            name = f"perf.latency_p{q}_ms"
            lo, hi = bootstrap_ci(arr, percentile_stat(q), n_boot=n_boot, seed=seed)
            stats[name] = MetricStat(
                metric=name, n=arr.size, mean=float(np.percentile(arr, q)), ci_low=lo, ci_high=hi,
                direction="lower", kind="run",
            )  # fmt: skip
    for metric, series in run_vals.items():
        arr = np.array(series, dtype=float)
        lo, hi = bootstrap_ci(arr, n_boot=n_boot, seed=seed)
        stats[metric] = MetricStat(
            metric=metric, n=arr.size, mean=float(arr.mean()), ci_low=lo, ci_high=hi,
            direction="lower", kind="run",
        )  # fmt: skip
    return dict(sorted(stats.items()))


def summarize(results: list[CaseResult], *, n_boot: int = 1000, seed: int = 0) -> RunSummary:
    status_counts: dict[str, int] = defaultdict(int)
    for r in results:
        status_counts[str(r.status)] += 1
    verdicts = [r.passed for r in results if r.passed is not None]
    costs = [r.output.cost_usd for r in results if r.output.cost_usd is not None]
    by_suite: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_suite[r.suite].append(r)
    judge_cost = sum(s.judge_cost_usd or 0.0 for r in results for s in r.scores)
    return RunSummary(
        n_cases=len({(r.suite, r.case_id) for r in results}),
        n_results=len(results),
        status_counts=dict(status_counts),
        pass_rate=(sum(verdicts) / len(verdicts)) if verdicts else None,
        metrics=_metric_stats(results, n_boot=n_boot, seed=seed),
        by_suite={
            suite: _metric_stats(rows, n_boot=max(200, n_boot // 4), seed=seed)
            for suite, rows in sorted(by_suite.items())
        },
        total_cost_usd=float(sum(costs)) if costs else None,
        judge_cost_usd=float(judge_cost),
        cost_unknown_results=sum(1 for r in results if r.output.cost_usd is None),
    )


def format_metric(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric.endswith("_ms"):
        return f"{value:,.0f} ms"
    if metric.startswith("cost."):
        sign, amount = ("-" if value < 0 else ""), abs(value)
        return f"{sign}${amount:,.4f}" if amount < 1 else f"{sign}${amount:,.2f}"
    if metric.startswith("perf.") and "tokens" in metric:
        return f"{value:,.0f}"
    return f"{value:.3f}"
