from __future__ import annotations

import pytest

from provingground.config import JudgeConfig
from provingground.models import TargetInfo
from provingground.providers import ProviderRegistry
from provingground.scorers import ScoreContext, SimJudge, build_judge


@pytest.fixture
def registry() -> ProviderRegistry:
    return ProviderRegistry()


@pytest.fixture
def sim_judge() -> SimJudge:
    return SimJudge()


@pytest.fixture
def score_ctx(sim_judge: SimJudge) -> ScoreContext:
    target = TargetInfo(
        name="t",
        kind="model",
        fingerprint="fp",
        model="sim/model-a",
        canaries=["PG-CANARY-TEST-1234"],
        forbidden_terms=["RivalMart"],
    )
    return ScoreContext(target=target, judge=sim_judge, suite="test")


@pytest.fixture
def llm_judge_sim(registry: ProviderRegistry) -> object:
    return build_judge(JudgeConfig(model="sim/judge"), registry)
