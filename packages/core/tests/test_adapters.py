"""Adapter wiring tests. Skipped unless the `adapters` extra is installed."""

from __future__ import annotations

import importlib.util

import pytest

from provingground.config import ScorerSpec
from provingground.models import TargetInfo, TargetOutput, TestCase
from provingground.scorers import ScoreContext, build_scorer
from provingground.scorers.adapters.stubs import StubJudge, deepeval_stub_llm, ragas_stub_llm

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("deepeval", "ragas")),
    reason="needs `uv sync --extra adapters`",
)

DOC = "Footwear can be returned within 30 days of delivery if it is unworn and in the original box."


def ctx() -> ScoreContext:
    return ScoreContext(
        target=TargetInfo(name="t", kind="model", fingerprint="f"), judge=StubJudge(), suite="s"
    )


def case() -> TestCase:
    return TestCase(id="c", input="How long do I have to return boots?", expected="30 days", context=[DOC])


async def test_ragas_faithfulness_distinguishes_supported_from_hallucinated() -> None:
    scorer = build_scorer(
        ScorerSpec(name="ragas", params={"metric": "faithfulness", "llm": ragas_stub_llm()})
    )
    good = TargetOutput(text="Footwear can be returned within 30 days.", retrieved_context=[DOC])
    bad = TargetOutput(text="Footwear can be returned within 90 days.", retrieved_context=[DOC])
    (s_good,) = await scorer.score(case(), good, ctx())
    (s_bad,) = await scorer.score(case(), bad, ctx())
    assert s_good.metric == "factuality.ragas_faithfulness"
    assert s_good.value == 1.0 and s_good.passed
    assert s_bad.value == 0.0 and not s_bad.passed
    assert s_good.details["framework"] == "ragas"


async def test_deepeval_metrics_run_through_the_scorer() -> None:
    llm = deepeval_stub_llm()
    for metric, expected_metric in [
        ("answer_relevancy", "quality.deepeval_answer_relevancy"),
        ("faithfulness", "factuality.deepeval_faithfulness"),
        ("hallucination", "factuality.deepeval_hallucination_rate"),
    ]:
        scorer = build_scorer(ScorerSpec(name="deepeval", params={"metric": metric, "llm": llm}))
        out = TargetOutput(text="Footwear can be returned within 30 days.", retrieved_context=[DOC])
        (score,) = await scorer.score(case(), out, ctx())
        assert score.metric == expected_metric
        assert score.value is not None and 0.0 <= score.value <= 1.0
        assert score.details["framework"] == "deepeval"


async def test_deepeval_geval_metric_name_includes_label() -> None:
    scorer = build_scorer(
        ScorerSpec(
            name="deepeval",
            params={
                "metric": "geval",
                "name": "politeness",
                "criteria": "Is it polite?",
                "llm": deepeval_stub_llm(),
            },
        )
    )
    assert scorer.metrics == ("quality.deepeval_geval_politeness",)
    (score,) = await scorer.score(case(), TargetOutput(text="Happy to help!"), ctx())
    assert score.value is not None


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown deepeval metric"):
        build_scorer(ScorerSpec(name="deepeval", params={"metric": "nope"}))
    with pytest.raises(ValueError, match="unknown ragas metric"):
        build_scorer(ScorerSpec(name="ragas", params={"metric": "nope"}))


async def test_platform_llm_routes_deepeval_calls_through_the_judge_and_records_cost() -> None:
    from provingground.scorers.adapters.common import UsageSink
    from provingground.scorers.adapters.deepeval_adapter import make_platform_llm

    sink = UsageSink()
    llm = make_platform_llm(StubJudge(), sink)
    from pydantic import BaseModel

    class Reply(BaseModel):
        verdict: str
        reason: str

    result = await llm.a_generate("Is the sky blue?", schema=Reply)
    assert isinstance(result, Reply)
    assert sink.calls == 1 and sink.model == "sim/judge"
