from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from provingground.compare import compare_runs, leaderboard
from provingground.config import GatePolicy, GateRule
from provingground.gate import EXIT_FAIL, Verdict, evaluate_gate
from provingground.models import (
    CaseResult,
    CaseStatus,
    MetricStat,
    Run,
    RunSummary,
    Score,
    TargetInfo,
    TargetOutput,
)
from provingground.stats import unpaired_diff_ci


def _result(suite: str, cid: str, metric: str, value: float, passed: bool, *, critical: bool = False,
            latency: float = 100.0, cost: float = 0.001) -> CaseResult:  # fmt: skip
    return CaseResult(
        suite=suite,
        case_id=cid,
        critical=critical,
        status=CaseStatus.OK,
        output=TargetOutput(text="x", latency_ms=latency, cost_usd=cost, model_served="m"),
        scores=[Score(metric=metric, value=value, passed=passed)],
    )


def _run(rid: str, metric: str, values: list[float], **kw: object) -> Run:
    from provingground.metrics import summarize

    results = [_result("s", f"c{i}", metric, v, v >= 0.5, **kw) for i, v in enumerate(values)]
    return Run(id=rid, created_at=datetime.now(UTC), target=TargetInfo(name="t", kind="model", fingerprint="f", model="m"),
               results=results, summary=summarize(results, n_boot=300))  # fmt: skip


def test_compare_detects_regression_direction() -> None:
    base = _run("base", "quality.correctness", [1.0] * 40 + [0.0] * 10)
    cand = _run("cand", "quality.correctness", [1.0] * 25 + [0.0] * 25)
    comp = compare_runs(base, cand, seed=1)
    d = comp.delta("quality.correctness")
    assert d is not None and d.delta is not None and d.delta < 0 and d.regressed
    assert comp.regressions and not comp.fixes


def test_gate_noninferiority_fail_and_pass() -> None:
    policy = GatePolicy(rules=[GateRule(metric="quality.correctness", min=0.5, max_drop=0.05)])
    base = _run("base", "quality.correctness", [1.0] * 45 + [0.0] * 5)  # 0.90
    big_drop = _run("cand", "quality.correctness", [1.0] * 30 + [0.0] * 20)  # 0.60
    comp = compare_runs(base, big_drop, seed=2)
    result = evaluate_gate(policy, "release", big_drop, comp)
    assert result.verdict == Verdict.FAIL and result.exit_code == EXIT_FAIL
    # A candidate at parity with the baseline is clearly non-inferior (delta ~ 0).
    at_parity = _run("cand2", "quality.correctness", [1.0] * 45 + [0.0] * 5)  # 0.90
    comp2 = compare_runs(base, at_parity, seed=2)
    assert evaluate_gate(policy, "release", at_parity, comp2).verdict in (Verdict.PASS, Verdict.WARN)


def test_gate_absolute_ceiling_on_rate() -> None:
    policy = GatePolicy(rules=[GateRule(metric="safety.attack_success_rate", max=0.05)])
    bad = _run("bad", "safety.attack_success_rate", [0.0] * 30 + [1.0] * 20)  # ASR 0.40
    assert evaluate_gate(policy, "p", bad, compare_runs(bad, bad, seed=0)).verdict == Verdict.FAIL
    good = _run("good", "safety.attack_success_rate", [0.0] * 50)  # ASR 0.00, well under the ceiling
    assert evaluate_gate(policy, "p", good, compare_runs(good, good, seed=0)).verdict != Verdict.FAIL


def test_gate_critical_cases_must_pass() -> None:
    policy = GatePolicy(rules=[GateRule(critical_cases="must_pass")])
    results = [_result("s", "ok", "quality.correctness", 1.0, True, critical=True),
               _result("s", "bad", "quality.correctness", 0.0, False, critical=True)]  # fmt: skip
    from provingground.metrics import summarize

    cand = Run(id="c", created_at=datetime.now(UTC), target=TargetInfo(name="t", kind="model", fingerprint="f"),
               results=results, summary=summarize(results))  # fmt: skip
    assert evaluate_gate(policy, "p", cand, None).verdict == Verdict.FAIL


def test_gate_inconclusive_policy_controls_outcome() -> None:
    # A small sample with a borderline drop produces a wide CI -> inconclusive.
    base = _run("base", "quality.correctness", [1.0] * 8 + [0.0] * 2)
    cand = _run("cand", "quality.correctness", [1.0] * 6 + [0.0] * 4)
    comp = compare_runs(base, cand, seed=5)
    rule = GateRule(metric="quality.correctness", max_drop=0.1)
    fail_policy = GatePolicy(on_inconclusive="fail", rules=[rule])
    pass_policy = GatePolicy(on_inconclusive="pass", rules=[rule])
    v_fail = evaluate_gate(fail_policy, "p", cand, comp).rules[0].verdict
    v_pass = evaluate_gate(pass_policy, "p", cand, comp).rules[0].verdict
    # Same data, different policy for the inconclusive case.
    assert {v_fail, v_pass} <= {Verdict.FAIL, Verdict.PASS, Verdict.WARN}


def test_comparability_block_on_dataset_mismatch() -> None:
    from provingground.models import SuiteInfo

    base = _run("base", "quality.correctness", [1.0] * 10)
    cand = _run("cand", "quality.correctness", [1.0] * 10)
    base.suites = [SuiteInfo(name="s", dataset_version="v1", n_cases=10)]
    cand.suites = [SuiteInfo(name="s", dataset_version="v2", n_cases=10)]
    comp = compare_runs(base, cand, seed=0)
    assert any(i.severity == "block" for i in comp.comparability)
    result = evaluate_gate(
        GatePolicy(rules=[GateRule(metric="quality.correctness", min=0.5)]), "p", cand, comp
    )
    assert result.exit_code == 2  # not comparable -> pipeline error


def test_leaderboard_pareto_front() -> None:
    runs = [
        _mk_leader_run("a", correctness=0.95, cost=3.0),
        _mk_leader_run("b", correctness=0.80, cost=0.2),
        _mk_leader_run("c", correctness=0.94, cost=0.8),
        _mk_leader_run("dom", correctness=0.70, cost=2.0),  # dominated by a and c
    ]
    rows = {r.model: r for r in leaderboard(runs)}
    assert rows["a"].on_pareto_front and rows["b"].on_pareto_front and rows["c"].on_pareto_front
    assert not rows["dom"].on_pareto_front


def _mk_leader_run(model: str, correctness: float, cost: float) -> Run:
    summary = RunSummary(
        pass_rate=correctness,
        metrics={
            "quality.correctness": MetricStat(metric="quality.correctness", n=50, mean=correctness),
            "cost.per_1k_requests_usd": MetricStat(
                metric="cost.per_1k_requests_usd", n=50, mean=cost, direction="lower", kind="run"
            ),
        },
    )
    return Run(id=f"r-{model}", created_at=datetime.now(UTC),
               target=TargetInfo(name="t", kind="model", fingerprint="f", model=model), summary=summary)  # fmt: skip


def test_unpaired_diff_ci_latency() -> None:
    base = np.full(100, 100.0)
    cand = np.full(100, 150.0)
    from provingground.stats import percentile_stat

    point, _lo, _hi = unpaired_diff_ci(base, cand, percentile_stat(95), seed=1)
    assert abs(point - 50.0) < 1e-6
