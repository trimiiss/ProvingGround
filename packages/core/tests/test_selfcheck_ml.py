from __future__ import annotations

import importlib.util

import pytest

from provingground.datasets import LoadedSuite
from provingground.models import CaseStatus, TargetInfo, TargetOutput, TestCase
from provingground.runner import RunSettings, execute_run
from provingground.scorers import ScoreContext, SimJudge, build_scorer
from provingground.scorers.selfcheck import SelfCheck, lexical_support
from provingground.targets.base import InvokeContext, Target

ML = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("transformers") is not None


def test_lexical_support() -> None:
    sample = "Footwear can be returned within 30 days of delivery."
    assert lexical_support("Footwear can be returned within 30 days.", sample)
    assert not lexical_support("Footwear can be returned within 90 days.", sample)  # number mismatch
    assert not lexical_support("Tents come with a two year warranty.", sample)  # unrelated wording


async def test_selfcheck_flags_the_odd_one_out() -> None:
    ctx = ScoreContext(
        target=TargetInfo(name="t", kind="model", fingerprint="f"), judge=SimJudge(), suite="s"
    )
    good = "Footwear can be returned within 30 days of delivery."
    outputs = [
        TargetOutput(text=good),
        TargetOutput(text=good),
        TargetOutput(text="Footwear can be returned within 90 days of delivery."),
    ]
    scorer = SelfCheck()
    per_output = await scorer.score_group(TestCase(id="c", input="q"), outputs, ctx)
    flagged = [
        s.value
        for scores in per_output
        for s in scores
        if s.metric == "factuality.selfcheck_hallucination_rate"
    ]
    assert flagged == [0.0, 0.0, 1.0]  # only the divergent sample is a suspected hallucination


async def test_selfcheck_needs_repeats() -> None:
    ctx = ScoreContext(
        target=TargetInfo(name="t", kind="model", fingerprint="f"), judge=SimJudge(), suite="s"
    )
    assert await SelfCheck().score_group(
        TestCase(id="c", input="q"), [TargetOutput(text="x y z w")], ctx
    ) == [[]]


class FlakyTarget(Target):
    """Answers consistently except on repeat 2, where it changes a number."""

    @property
    def info(self) -> TargetInfo:
        return TargetInfo(name="flaky", kind="python", fingerprint="flaky")

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        days = 90 if ctx.repeat == 2 else 30
        return TargetOutput(text=f"Footwear can be returned within {days} days of delivery.", latency_ms=1.0)


async def test_runner_applies_group_scorers_across_repeats() -> None:
    suite = LoadedSuite(name="s", cases=[TestCase(id="c", input="q")], scorers=[build_scorer("selfcheck")],
                        dataset=None, dataset_version="v")  # fmt: skip
    run = await execute_run(
        FlakyTarget("flaky", None), [suite], RunSettings(repeats=3, use_cache=False, n_boot=0), SimJudge()
    )  # type: ignore[arg-type]
    by_repeat = {r.repeat: r.score("factuality.selfcheck_hallucination_rate").value for r in run.results}  # type: ignore[union-attr]
    assert by_repeat == {0: 0.0, 1: 0.0, 2: 1.0}
    assert all(r.status == CaseStatus.OK for r in run.results)


class StubEntailment:
    """Stands in for the NLI model: entailment iff every number in the claim is in the premise."""

    def probabilities(self, premises: list[str], claims: list[str]) -> list[list[dict[str, float]]]:
        from provingground import textutil

        rows = []
        for claim in claims:
            row = []
            for premise in premises:
                ok = set(textutil.numbers(claim)) <= set(textutil.numbers(premise))
                row.append(
                    {
                        "entailment": 0.95 if ok else 0.01,
                        "contradiction": 0.02 if ok else 0.95,
                        "neutral": 0.03,
                    }
                )
            rows.append(row)
        return rows


@pytest.mark.skipif(not ML, reason="needs `uv sync --extra ml`")
async def test_nli_faithfulness_logic_with_a_stub_model() -> None:
    ctx = ScoreContext(
        target=TargetInfo(name="t", kind="model", fingerprint="f"), judge=SimJudge(), suite="s"
    )
    scorer = build_scorer({"name": "nli_faithfulness", "params": {"entailment": StubEntailment()}})
    case = TestCase(id="c", input="q", context=["Footwear can be returned within 30 days of delivery."])
    supported = TargetOutput(text="Footwear can be returned within 30 days.")
    wrong = TargetOutput(text="Footwear can be returned within 90 days.")
    s1 = await scorer.score(case, supported, ctx)
    s2 = await scorer.score(case, wrong, ctx)
    assert s1[0].value == 1.0 and s1[1].value == 0.0
    assert s2[0].value == 0.0 and s2[1].value == 1.0 and "90 days" in s2[0].reason
    abstain = await scorer.score(
        case, TargetOutput(text="I don't have that information in the documents."), ctx
    )
    assert abstain[0].value == 1.0  # nothing to verify


@pytest.mark.ml
@pytest.mark.skipif(not ML, reason="needs `uv sync --extra ml` and downloads the NLI model")
async def test_real_nli_model_separates_supported_from_contradicted() -> None:
    ctx = ScoreContext(
        target=TargetInfo(name="t", kind="model", fingerprint="f"), judge=SimJudge(), suite="s"
    )
    scorer = build_scorer("nli_faithfulness")
    case = TestCase(
        id="c", input="q", context=["Footwear can be returned within 30 days of delivery if it is unworn."]
    )
    good = await scorer.score(case, TargetOutput(text="Footwear can be returned within 30 days."), ctx)
    bad = await scorer.score(case, TargetOutput(text="Footwear can be returned within 90 days."), ctx)
    assert good[0].passed and not bad[0].passed


def test_ml_scorer_gives_a_clear_error_without_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from provingground.scorers.ml import models

    monkeypatch.setattr(models, "ml_available", lambda: False)
    with pytest.raises(models.MLNotInstalled, match="provingground\\[ml\\]"):
        build_scorer("nli_faithfulness")
