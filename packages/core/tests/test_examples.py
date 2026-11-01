"""The newcomer example must keep doing what its README promises."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from provingground.config import load_config
from provingground.engine import gate_target, settings_from_config
from provingground.gate import Verdict

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "my-first-bot"


async def test_my_first_bot_is_blocked_for_both_planted_bugs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    cfg = load_config(EXAMPLE / "provingground.yaml")
    settings = settings_from_config(cfg, use_cache=False, cache_dir=tmp_path)
    try:
        result, _, candidate, _ = await gate_target(cfg, "shop-bot", "release", settings=settings)
    finally:
        sys.modules.pop("bot", None)
    assert result.verdict == Verdict.FAIL
    assert {r.rule for r in result.failing} == {
        "quality.correctness",
        "safety.attack_success_rate",
        "critical cases must pass",
    }
    assert {r.case_id for r in candidate.results if r.passed is False} == {"refund-time", "fake-admin"}
