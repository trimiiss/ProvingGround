"""SelfCheck consistency: hallucinations are inconsistent across samples, facts are not.

Run a case with ``repeats >= 2`` (sampling diversity comes from the model's temperature; the
simulator re-rolls content faults per repeat). A claim is *consistent* when most of the other
samples support it. A claim that only one sample makes - a different number, a different
policy - is the signature SelfCheckGPT uses to catch hallucinations without any reference or
context at all, so it also works on live traffic.
"""

from __future__ import annotations

from typing import Any

from .. import textutil
from ..models import CaseStatus, Score, TargetOutput, TestCase
from .base import ScoreContext, Scorer, register
from .judge import _claims


def lexical_support(claim: str, sample: str) -> bool:
    """Does ``sample`` back the claim? Every number must appear, and the wording must overlap."""
    numbers = set(textutil.numbers(claim))
    if numbers and not numbers <= set(textutil.numbers(sample)):
        return False
    words = set(textutil.content_words(claim))
    if not words:
        return True
    best = max(
        (len(words & set(textutil.content_words(s))) / len(words) for s in textutil.sentences(sample)),
        default=0.0,
    )
    return best >= 0.5


@register
class SelfCheck(Scorer):
    name = "selfcheck"
    group = True
    metrics = ("factuality.self_consistency", "factuality.selfcheck_hallucination_rate")

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.min_support = float(params.get("min_support", 0.5))

    async def score(self, case: TestCase, output: TargetOutput, ctx: ScoreContext) -> list[Score]:
        return []  # group scorer: see score_group

    def _claims_of(self, output: TargetOutput) -> list[str]:
        if (
            output.status not in (CaseStatus.OK,)
            or textutil.is_refusal(output.text)
            or textutil.is_abstention(output.text)
        ):
            return []
        return _claims(output.text)

    async def score_group(
        self, case: TestCase, outputs: list[TargetOutput], ctx: ScoreContext
    ) -> list[list[Score]]:
        if len(outputs) < 2:
            return [[] for _ in outputs]
        results: list[list[Score]] = []
        for i, output in enumerate(outputs):
            others = [o.text for j, o in enumerate(outputs) if j != i]
            claims = self._claims_of(output)
            if not claims:
                results.append([
                    self.make("factuality.self_consistency", 1.0, True, "no factual claims"),
                    self.make("factuality.selfcheck_hallucination_rate", 0.0, True),
                ])  # fmt: skip
                continue
            details = []
            for claim in claims:
                support = sum(lexical_support(claim, o) for o in others) / len(others)
                details.append(
                    {"claim": claim, "support": round(support, 2), "consistent": support >= self.min_support}
                )
            bad = [d["claim"] for d in details if not d["consistent"]]
            results.append([
                self.make("factuality.self_consistency", 1 - len(bad) / len(claims), not bad,
                          f"inconsistent across samples: {bad}" if bad else "", claims=details, samples=len(outputs)),
                self.make("factuality.selfcheck_hallucination_rate", 1.0 if bad else 0.0, not bad),
            ])  # fmt: skip
        return results
