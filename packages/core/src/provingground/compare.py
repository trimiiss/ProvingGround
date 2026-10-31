"""Compare two (or more) runs: paired metric deltas with CIs, flips, and comparability checks.

Both runs answer the same cases, so comparisons are *paired per case* wherever possible: the
delta of a metric is estimated by resampling cases, not by subtracting two independent means.
Latency percentiles, which have no per-case pairing, use an unpaired difference.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel

from .metrics import direction, is_run_level
from .models import SCORABLE_STATUSES, CaseStatus, Run
from .stats import mcnemar_exact, paired_bootstrap_diff, percentile_stat, unpaired_diff_ci

CaseKey = tuple[str, str]


@dataclass
class CaseAggregate:
    """One case's results in a run, collapsed over repeats."""

    suite: str
    case_id: str
    critical: bool
    statuses: list[CaseStatus] = field(default_factory=list)
    scores: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    passes: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))
    latencies: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    overall_pass: list[bool] = field(default_factory=list)
    preview: str = ""
    text: str = ""

    @property
    def key(self) -> CaseKey:
        return (self.suite, self.case_id)

    @property
    def error_fraction(self) -> float:
        bad = sum(
            s in (CaseStatus.ERROR, CaseStatus.TIMEOUT, CaseStatus.MODEL_MISMATCH) for s in self.statuses
        )
        return bad / len(self.statuses) if self.statuses else 0.0

    def metric_value(self, metric: str) -> float | None:
        if metric == "perf.latency_ms":
            return float(np.mean(self.latencies)) if self.latencies else None
        if metric == "cost.per_1k_requests_usd":
            return float(np.mean(self.costs)) * 1000 if self.costs else None
        if metric == "reliability.error_rate":
            return self.error_fraction if self.statuses else None
        vals = self.scores.get(metric)
        return float(np.mean(vals)) if vals else None

    def case_passed(self, metric: str | None = None) -> bool | None:
        verdicts = self.passes.get(metric, []) if metric else self.overall_pass
        return all(verdicts) if verdicts else None


def aggregate_cases(run: Run) -> dict[CaseKey, CaseAggregate]:
    aggs: dict[CaseKey, CaseAggregate] = {}
    for r in run.results:
        agg = aggs.setdefault((r.suite, r.case_id), CaseAggregate(r.suite, r.case_id, r.critical))
        agg.statuses.append(r.status)
        agg.preview = agg.preview or r.input_preview
        if r.status in SCORABLE_STATUSES:
            agg.text = agg.text or r.output.text
            if r.output.latency_ms is not None:
                agg.latencies.append(r.output.latency_ms)
            if r.output.cost_usd is not None:
                agg.costs.append(r.output.cost_usd)
            if r.passed is not None:
                agg.overall_pass.append(r.passed)
            for s in r.scores:
                if s.value is not None:
                    agg.scores[s.metric].append(s.value)
                if s.passed is not None:
                    agg.passes[s.metric].append(s.passed)
    return aggs


class MetricDelta(BaseModel):
    metric: str
    direction: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    n: int = 0
    improved: bool | None = None  # did the candidate move in the better direction (point estimate)?

    @property
    def regressed(self) -> bool:
        return self.improved is False


class Flip(BaseModel):
    suite: str
    case_id: str
    critical: bool
    kind: str  # "regression" (pass -> fail) or "fix" (fail -> pass)
    preview: str
    baseline_text: str
    candidate_text: str


class ComparabilityIssue(BaseModel):
    severity: str  # "block" | "warn"
    message: str


class Comparison(BaseModel):
    baseline_id: str
    candidate_id: str
    baseline_label: str = "baseline"
    candidate_label: str = "candidate"
    target: str
    n_common_cases: int
    n_baseline_only: int
    n_candidate_only: int
    confidence: float
    deltas: dict[str, MetricDelta] = {}
    flips: list[Flip] = []
    comparability: list[ComparabilityIssue] = []
    noise_floor: dict[str, float] = {}

    def delta(self, metric: str) -> MetricDelta | None:
        return self.deltas.get(metric)

    @property
    def regressions(self) -> list[Flip]:
        return [f for f in self.flips if f.kind == "regression"]

    @property
    def fixes(self) -> list[Flip]:
        return [f for f in self.flips if f.kind == "fix"]


def _comparable_metrics(baseline: Run, candidate: Run) -> list[str]:
    metrics: set[str] = set()
    for run in (baseline, candidate):
        for m in run.summary.metrics:
            if m.startswith("perf.latency_p") or m == "perf.latency_mean_ms":
                metrics.add("perf.latency_ms")
            elif m.startswith("perf.") and "tokens" in m:
                continue
            else:
                metrics.add(m)
    return sorted(metrics)


def _latency_arrays(aggs: dict[CaseKey, CaseAggregate], keys: list[CaseKey]) -> np.ndarray:
    return np.array([float(np.mean(aggs[k].latencies)) for k in keys if aggs[k].latencies], dtype=float)


def compare_runs(
    baseline: Run,
    candidate: Run,
    *,
    confidence: float = 0.95,
    seed: int = 0,
    max_flips: int = 50,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
) -> Comparison:
    base_aggs = aggregate_cases(baseline)
    cand_aggs = aggregate_cases(candidate)
    common = sorted(set(base_aggs) & set(cand_aggs))
    comp = Comparison(
        baseline_id=baseline.id, candidate_id=candidate.id, target=candidate.target.name,
        baseline_label=baseline_label, candidate_label=candidate_label, confidence=confidence,
        n_common_cases=len(common), n_baseline_only=len(set(base_aggs) - set(cand_aggs)),
        n_candidate_only=len(set(cand_aggs) - set(base_aggs)),
    )  # fmt: skip
    comp.comparability = check_comparability(baseline, candidate, comp)
    if not common:
        return comp

    for metric in _comparable_metrics(baseline, candidate):
        d = _metric_delta(metric, base_aggs, cand_aggs, common, confidence=confidence, seed=seed)
        if d is not None:
            comp.deltas[d.metric] = d  # latency is reported as perf.latency_p95_ms
            if d.ci_low is not None and d.ci_high is not None:
                comp.noise_floor[d.metric] = (d.ci_high - d.ci_low) / 2

    comp.flips = _flips(base_aggs, cand_aggs, common, max_flips)
    return comp


def _metric_delta(
    metric: str,
    base: dict[CaseKey, CaseAggregate],
    cand: dict[CaseKey, CaseAggregate],
    common: list[CaseKey],
    *,
    confidence: float,
    seed: int,
) -> MetricDelta | None:
    if metric == "perf.latency_ms":
        return _latency_delta(base, cand, common, confidence=confidence, seed=seed)
    pairs = [(base[k].metric_value(metric), cand[k].metric_value(metric)) for k in common]
    pairs = [(b, c) for b, c in pairs if b is not None and c is not None]
    if not pairs:
        return None
    b_arr = np.array([b for b, _ in pairs], dtype=float)
    c_arr = np.array([c for _, c in pairs], dtype=float)
    point, lo, hi = paired_bootstrap_diff(b_arr, c_arr, conf=confidence, seed=seed)
    dirn = direction(metric)
    improved = point > 1e-9 if dirn == "higher" else point < -1e-9 if point else None
    p_value = None
    if not is_run_level(metric):  # pass/fail metric -> McNemar on discordant cases
        n01 = n10 = 0
        for k in common:
            bp, cp = base[k].case_passed(metric), cand[k].case_passed(metric)
            if bp is None or cp is None:
                continue
            n01 += bp and not cp
            n10 += (not bp) and cp
        if n01 + n10:
            p_value = mcnemar_exact(n01, n10)
    return MetricDelta(
        metric=metric, direction=dirn, baseline=float(b_arr.mean()), candidate=float(c_arr.mean()),
        delta=point, ci_low=lo, ci_high=hi, p_value=p_value, n=len(pairs),
        improved=None if point == 0 else improved,
    )  # fmt: skip


def _latency_delta(
    base: dict[CaseKey, CaseAggregate],
    cand: dict[CaseKey, CaseAggregate],
    common: list[CaseKey],
    *,
    confidence: float,
    seed: int,
) -> MetricDelta | None:
    b_lat = _latency_arrays(base, common)
    c_lat = _latency_arrays(cand, common)
    if b_lat.size == 0 or c_lat.size == 0:
        return None
    # Report p95 as the headline latency delta (unpaired: the percentile has no per-case pairing).
    point, lo, hi = unpaired_diff_ci(b_lat, c_lat, percentile_stat(95), conf=confidence, seed=seed)
    return MetricDelta(
        metric="perf.latency_p95_ms", direction="lower",
        baseline=float(np.percentile(b_lat, 95)), candidate=float(np.percentile(c_lat, 95)),
        delta=point, ci_low=lo, ci_high=hi, n=min(b_lat.size, c_lat.size),
        improved=point < 0 if point else None,
    )  # fmt: skip


def _flips(
    base: dict[CaseKey, CaseAggregate], cand: dict[CaseKey, CaseAggregate], common: list[CaseKey], limit: int
) -> list[Flip]:
    flips: list[Flip] = []
    for k in common:
        b, c = base[k].case_passed(), cand[k].case_passed()
        if b is None or c is None or b == c:
            continue
        flips.append(Flip(
            suite=k[0], case_id=k[1], critical=cand[k].critical,
            kind="regression" if b and not c else "fix",
            preview=cand[k].preview[:200], baseline_text=base[k].text[:200], candidate_text=cand[k].text[:200],
        ))  # fmt: skip
    flips.sort(key=lambda f: (f.kind != "regression", not f.critical, f.suite, f.case_id))
    return flips[:limit]


def check_comparability(baseline: Run, candidate: Run, comp: Comparison) -> list[ComparabilityIssue]:
    issues: list[ComparabilityIssue] = []
    b_suites, c_suites = baseline.suite_versions(), candidate.suite_versions()
    for suite in set(b_suites) & set(c_suites):
        if b_suites[suite] != c_suites[suite]:
            issues.append(ComparabilityIssue(severity="block",
                message=f"dataset {suite!r} differs: {b_suites[suite]} vs {c_suites[suite]}"))  # fmt: skip
    if set(b_suites) != set(c_suites):
        only = set(b_suites) ^ set(c_suites)
        issues.append(
            ComparabilityIssue(severity="warn", message=f"suites differ between runs: {sorted(only)}")
        )
    for scorer, bv in baseline.scorer_versions.items():
        cv = candidate.scorer_versions.get(scorer)
        if cv and cv != bv:
            issues.append(
                ComparabilityIssue(severity="block", message=f"scorer {scorer!r} version {bv} vs {cv}")
            )
    b_err = _error_rate(baseline)
    c_err = _error_rate(candidate)
    if abs(b_err - c_err) > 0.05:
        issues.append(ComparabilityIssue(severity="warn",
            message=f"error-rate gap {b_err:.1%} vs {c_err:.1%} may bias scored subsets"))  # fmt: skip
    b_cache, c_cache = _cache_share(baseline), _cache_share(candidate)
    if abs(b_cache - c_cache) > 0.5:
        issues.append(ComparabilityIssue(severity="warn",
            message=f"cache-hit share differs ({b_cache:.0%} vs {c_cache:.0%}); latency/cost may be an artifact"))  # fmt: skip
    return issues


def _error_rate(run: Run) -> float:
    bad = sum(run.summary.status_counts.get(s, 0) for s in ("error", "timeout", "model_mismatch"))
    return bad / run.summary.n_results if run.summary.n_results else 0.0


def _cache_share(run: Run) -> float:
    if not run.results:
        return 0.0
    return sum(r.output.cached for r in run.results) / len(run.results)


# --- N-way leaderboard -----------------------------------------------------------------------


class LeaderboardRow(BaseModel):
    target: str
    model: str | None
    run_id: str
    pass_rate: float | None
    metrics: dict[str, float] = {}
    cost_per_1k: float | None = None
    latency_p95_ms: float | None = None
    on_pareto_front: bool = False


def leaderboard(runs: list[Run], *, quality_metric: str = "quality.correctness") -> list[LeaderboardRow]:
    """Rank runs and flag the quality-vs-cost Pareto front (maximize quality, minimize cost)."""
    rows: list[LeaderboardRow] = []
    for run in runs:
        s = run.summary
        quality = s.metrics[quality_metric].mean if quality_metric in s.metrics else s.pass_rate
        rows.append(LeaderboardRow(
            target=run.target.name, model=run.target.model, run_id=run.id, pass_rate=s.pass_rate,
            metrics={m: st.mean for m, st in s.metrics.items() if st.mean is not None and st.kind == "case"},
            cost_per_1k=s.metrics["cost.per_1k_requests_usd"].mean if "cost.per_1k_requests_usd" in s.metrics else None,
            latency_p95_ms=s.metrics["perf.latency_p95_ms"].mean if "perf.latency_p95_ms" in s.metrics else None,
        ))  # fmt: skip
        rows[-1].metrics.setdefault("_quality", quality or 0.0)
    for row in rows:
        q, c = row.metrics.get("_quality", 0.0), (row.cost_per_1k if row.cost_per_1k is not None else 0.0)
        dominated = any(
            other is not row
            and other.metrics.get("_quality", 0.0) >= q
            and (other.cost_per_1k if other.cost_per_1k is not None else 0.0) <= c
            and (
                other.metrics.get("_quality", 0.0) > q
                or (other.cost_per_1k if other.cost_per_1k is not None else 0.0) < c
            )
            for other in rows
        )
        row.on_pareto_front = not dominated
    for row in rows:
        row.metrics.pop("_quality", None)
    rows.sort(key=lambda r: r.metrics.get(quality_metric, r.pass_rate or 0.0), reverse=True)
    return rows
