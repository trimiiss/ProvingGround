"""The headline Phase 2 check: switching support-bot from model A to B must BLOCK; to C must PASS.

Runs the real demo agent through the real gate on the real datasets (sampled for speed), using
A/B/C overrides so no git branches are needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from provingground.config import load_config
from provingground.engine import gate_target, settings_from_config
from provingground.gate import Verdict

ROOT = Path(__file__).resolve().parents[3]
# support-bot is a RAG assistant; these are its own suites.
SUITES = ["customer-quality", "customer-factuality", "security-rag", "over-refusal"]


@pytest.fixture(scope="module")
def cfg():
    return load_config(ROOT / "provingground.yaml")


async def _gate(cfg, candidate_model: str):
    settings = settings_from_config(cfg, use_cache=False, n_boot=400, seed=7)
    result, comparison, _candidate, _baseline = await gate_target(
        cfg,
        "support-bot",
        "release",
        suites=SUITES,
        settings=settings,
        candidate_overrides={"model": f"sim/{candidate_model}"},
        baseline_overrides={"model": "sim/model-a"},
    )
    return result, comparison


async def test_switch_to_budget_model_is_blocked(cfg) -> None:
    result, comparison = await _gate(cfg, "model-b")
    assert result.verdict == Verdict.FAIL
    assert result.exit_code == 1
    failing_metrics = {r.metric for r in result.failing}
    # The budget model regresses on factual faithfulness and/or jailbreak resistance.
    assert failing_metrics & {
        "factuality.faithfulness",
        "factuality.hallucination_rate",
        "safety.attack_success_rate",
    }
    assert comparison is not None and comparison.regressions


async def test_switch_to_balanced_model_passes(cfg) -> None:
    result, _ = await _gate(cfg, "model-c")
    assert result.verdict in (Verdict.PASS, Verdict.WARN)
    assert result.exit_code == 0
