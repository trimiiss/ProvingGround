"""The CI/CD quality gate: evaluate a GatePolicy against a candidate run and a baseline comparison.

A rule can set an absolute floor/ceiling on the candidate (an SLO, judged on the point
estimate), a non-inferiority margin versus the baseline (absolute or percentage), or forbid
regressions on critical cases. Regression rules use the paired confidence interval: if the CI
of the change straddles the margin the rule is **INCONCLUSIVE** (the eval cannot tell at this
sample size), and ``on_inconclusive`` decides whether that blocks.
Exit codes: 0 PASS/WARN, 1 FAIL, 2 pipeline error (e.g. runs not comparable).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from .compare import Comparison, MetricDelta, aggregate_cases
from .config import GatePolicy, GateRule
from .metrics import format_metric
from .models import Run


class Verdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


class RuleResult(BaseModel):
    rule: str
    metric: str | None
    verdict: Verdict
    severity: str
    baseline: float | None = None
    candidate: float | None = None
    threshold: str = ""
    detail: str = ""


class GateResult(BaseModel):
    policy: str
    target: str
    verdict: Verdict
    exit_code: int
    candidate_id: str
    baseline_id: str | None
    rules: list[RuleResult] = []
    comparability_blocks: list[str] = []
    notes: list[str] = []

    @property
    def failing(self) -> list[RuleResult]:
        return [r for r in self.rules if r.verdict == Verdict.FAIL]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.rules if r.verdict == Verdict.WARN]


def _resolve_margin(margin: float | str | None, baseline: float | None) -> float | None:
    """Turn a margin (absolute like 0.02, or percentage like '25%') into an absolute amount."""
    if margin is None:
        return None
    if isinstance(margin, str) and margin.strip().endswith("%"):
        if baseline is None:
            return None
        return abs(baseline) * float(margin.strip().rstrip("%")) / 100.0
    return abs(float(margin))


def _eval_absolute(rule: GateRule, delta: MetricDelta | None, cand_summary: Run) -> tuple[Verdict, str]:
    """Absolute floors/ceilings are SLOs: compare the point estimate (the CI is shown for context).

    The statistical caution lives in the regression rules. Applying a CI to an absolute ceiling
    would make small suites permanently inconclusive (65 attack cases can't certify ASR < 5%
    unless almost none succeed), which turns the gate into noise.
    """
    assert rule.metric
    stat = cand_summary.summary.metrics.get(rule.metric)
    value: float | None
    ci: tuple[float | None, float | None]
    if stat is None and rule.metric == "perf.latency_p95_ms" and delta is not None:
        value, ci = delta.candidate, (None, None)
    else:
        value = stat.mean if stat else (delta.candidate if delta else None)
        ci = (stat.ci_low, stat.ci_high) if stat else (None, None)
    if value is None:
        return Verdict.INCONCLUSIVE, "metric not measured"
    ci_text = ""
    if ci[0] is not None and ci[1] is not None:
        ci_text = f" (95% CI {format_metric(rule.metric, ci[0])}…{format_metric(rule.metric, ci[1])})"
    verdicts: list[Verdict] = []
    details: list[str] = []
    if rule.min is not None:
        v = Verdict.PASS if value >= rule.min else Verdict.FAIL
        verdicts.append(v)
        details.append(
            f"{format_metric(rule.metric, value)} vs min {format_metric(rule.metric, rule.min)}{ci_text}"
        )
    if rule.max is not None:
        v = Verdict.PASS if value <= rule.max else Verdict.FAIL
        verdicts.append(v)
        details.append(
            f"{format_metric(rule.metric, value)} vs max {format_metric(rule.metric, rule.max)}{ci_text}"
        )
    return _worst(verdicts), "; ".join(details)


def _eval_noninferiority(rule: GateRule, delta: MetricDelta | None) -> tuple[Verdict, str]:
    """max_drop / max_increase: candidate must not be worse than baseline beyond the margin."""
    if delta is None or delta.delta is None:
        return Verdict.INCONCLUSIVE, "no baseline data"
    assert rule.metric
    lower_better = delta.direction == "lower"
    margin_src = rule.max_increase if lower_better else rule.max_drop
    margin = _resolve_margin(margin_src, delta.baseline)
    if margin is None:
        return Verdict.PASS, ""
    # Worse means delta > +margin (lower-is-better) or delta < -margin (higher-is-better).
    lo, hi = delta.ci_low, delta.ci_high
    if lo is None or hi is None:
        lo = hi = delta.delta
    if lower_better:
        bound = margin
        verdict = Verdict.PASS if hi <= bound else Verdict.FAIL if lo > bound else Verdict.INCONCLUSIVE
    else:
        bound = -margin
        verdict = Verdict.PASS if lo >= bound else Verdict.FAIL if hi < bound else Verdict.INCONCLUSIVE
    arrow = format_metric(rule.metric, delta.delta)
    return verdict, f"Δ {arrow} vs margin ±{format_metric(rule.metric, margin)}"


def _eval_critical(candidate: Run, comparison: Comparison | None) -> tuple[Verdict, str, int]:
    """With a baseline, block on critical *regressions* only; without one, all critical must pass."""
    aggs = aggregate_cases(candidate)
    critical = [a for a in aggs.values() if a.critical]
    if not critical:
        return Verdict.PASS, "no critical cases", 0
    if comparison is not None:
        regressions = [f for f in comparison.regressions if f.critical]
        pre_existing = sum(1 for a in critical if a.case_passed() is False) - len(regressions)
        if regressions:
            cases = ", ".join(f"{f.case_id}" for f in regressions[:3])
            return Verdict.FAIL, f"{len(regressions)} critical case(s) regressed: {cases}", len(regressions)
        note = f"; {pre_existing} already failing in baseline" if pre_existing > 0 else ""
        return Verdict.PASS, f"no critical regressions{note}", 0
    failed = [a for a in critical if a.case_passed() is False]
    verdict = Verdict.FAIL if failed else Verdict.PASS
    return verdict, f"{len(critical) - len(failed)}/{len(critical)} critical cases pass", len(failed)


def _worst(verdicts: list[Verdict]) -> Verdict:
    for v in (Verdict.FAIL, Verdict.INCONCLUSIVE, Verdict.WARN):
        if v in verdicts:
            return v
    return Verdict.PASS if verdicts else Verdict.INCONCLUSIVE


def _apply_inconclusive(verdict: Verdict, policy: GatePolicy) -> Verdict:
    if verdict != Verdict.INCONCLUSIVE:
        return verdict
    return {"fail": Verdict.FAIL, "warn": Verdict.WARN, "pass": Verdict.PASS}[policy.on_inconclusive]


def evaluate_gate(
    policy: GatePolicy, policy_name: str, candidate: Run, comparison: Comparison | None
) -> GateResult:
    deltas = comparison.deltas if comparison else {}
    rule_results: list[RuleResult] = []
    for rule in policy.rules:
        if rule.critical_cases:
            verdict, detail, _ = _eval_critical(candidate, comparison)
            rule_results.append(RuleResult(rule=rule.label, metric=None, verdict=verdict, severity=rule.severity,
                                           detail=detail, threshold="critical cases"))  # fmt: skip
            continue
        delta = deltas.get(rule.metric or "")
        verdicts: list[Verdict] = []
        details: list[str] = []
        if rule.min is not None or rule.max is not None:
            v, d = _eval_absolute(rule, delta, candidate)
            verdicts.append(v)
            details.append(d)
        if rule.max_drop is not None or rule.max_increase is not None:
            v, d = _eval_noninferiority(rule, delta)
            verdicts.append(v)
            details.append(d)
        verdict = _apply_inconclusive(_worst(verdicts), policy)
        rule_results.append(RuleResult(
            rule=rule.label, metric=rule.metric, verdict=verdict, severity=rule.severity,
            baseline=delta.baseline if delta else None, candidate=delta.candidate if delta else None,
            threshold=_threshold_text(rule), detail="; ".join(d for d in details if d),
        ))  # fmt: skip

    blocks = [i.message for i in (comparison.comparability if comparison else []) if i.severity == "block"]
    verdict, exit_code = _overall(rule_results, blocks)
    notes = []
    if comparison:
        notes += [f"⚠ {i.message}" for i in comparison.comparability if i.severity == "warn"]
        notes.append(f"{comparison.n_common_cases} cases compared ({comparison.n_baseline_only} baseline-only, "
                     f"{comparison.n_candidate_only} candidate-only)")  # fmt: skip
    return GateResult(
        policy=policy_name, target=candidate.target.name, verdict=verdict, exit_code=exit_code,
        candidate_id=candidate.id, baseline_id=comparison.baseline_id if comparison else None,
        rules=rule_results, comparability_blocks=blocks, notes=notes,
    )  # fmt: skip


def _threshold_text(rule: GateRule) -> str:
    parts = []
    if rule.min is not None:
        parts.append(f"≥{format_metric(rule.metric or '', rule.min)}")
    if rule.max is not None:
        parts.append(f"≤{format_metric(rule.metric or '', rule.max)}")
    if rule.max_drop is not None:
        parts.append(f"max drop {rule.max_drop}")
    if rule.max_increase is not None:
        parts.append(f"max +{rule.max_increase}")
    return ", ".join(parts)


def _overall(rules: list[RuleResult], blocks: list[str]) -> tuple[Verdict, int]:
    if blocks:
        return Verdict.FAIL, EXIT_ERROR  # runs aren't comparable: a pipeline problem, not a model verdict
    if any(r.verdict == Verdict.FAIL and r.severity == "block" for r in rules):
        return Verdict.FAIL, EXIT_FAIL
    if any((r.verdict in (Verdict.FAIL, Verdict.WARN)) for r in rules):
        return Verdict.WARN, EXIT_OK
    return Verdict.PASS, EXIT_OK
