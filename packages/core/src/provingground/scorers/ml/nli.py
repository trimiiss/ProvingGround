"""NLI-based hallucination detection (PyTorch).

Each factual claim in an answer is checked against the documents the agent was given with a
cross-encoder NLI model: a claim is *supported* when some document entails it. Unlike a
lexical check, an NLI model catches contradictions that reuse the document's words
("within 90 days" vs "within 30 days").
"""

from __future__ import annotations

import asyncio
from typing import Any

from ... import textutil
from ...models import Score, TargetOutput, TestCase
from ..base import ScoreContext, Scorer, register
from ..judge import _claims
from .models import DEFAULT_NLI, get_model, require_ml


class Entailment:
    """Scores (premise, claim) pairs. Thin wrapper so tests can substitute a stub."""

    def __init__(self, model: str = DEFAULT_NLI) -> None:
        self.model_name = model
        self._clf = get_model(model, max_length=384)

    def probabilities(self, premises: list[str], claims: list[str]) -> list[list[dict[str, float]]]:
        """matrix[c][p] = NLI class probabilities for claim c against premise p."""
        pairs_p = [p for _ in claims for p in premises]
        pairs_c = [c for c in claims for _ in premises]
        flat = self._clf.predict(pairs_p, pairs_c)
        n = len(premises)
        return [flat[i * n : (i + 1) * n] for i in range(len(claims))]


def support_scores(matrix: list[list[dict[str, float]]]) -> list[tuple[float, float]]:
    """Per claim: (best entailment over premises, worst contradiction over premises)."""
    out = []
    for row in matrix:
        out.append(
            (max(p.get("entailment", 0.0) for p in row), max(p.get("contradiction", 0.0) for p in row))
        )
    return out


def premises_for(texts: list[str]) -> list[str]:
    """Whole documents plus their sentences (documents here are short; sentences help long ones)."""
    seen: dict[str, None] = {}
    for text in texts:
        seen[text.strip()] = None
        for sentence in textutil.sentences(text):
            seen[sentence] = None
    return [t for t in seen if t]


@register
class NliFaithfulness(Scorer):
    name = "nli_faithfulness"
    metrics = ("factuality.nli_faithfulness", "factuality.nli_hallucination_rate")
    requires = frozenset({"context"})

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        require_ml()
        self.threshold = float(params.get("threshold", 0.5))
        self._entailment: Entailment | None = params.get("entailment")
        self.model_name = str(params.get("model", DEFAULT_NLI))

    def _model(self) -> Entailment:
        if self._entailment is None:
            self._entailment = Entailment(self.model_name)
        return self._entailment

    async def score(self, case: TestCase, output: TargetOutput, ctx: ScoreContext) -> list[Score]:
        text = output.text
        docs = output.retrieved_context or case.context or []
        claims = [] if (textutil.is_refusal(text) or textutil.is_abstention(text)) else _claims(text)
        if not claims:
            return [
                self.make("factuality.nli_faithfulness", 1.0, True, "no factual claims"),
                self.make("factuality.nli_hallucination_rate", 0.0, True),
            ]
        premises = premises_for(docs)
        matrix = await asyncio.to_thread(self._model().probabilities, premises, claims)
        details = []
        supported = 0
        for claim, (entail, contra) in zip(claims, support_scores(matrix), strict=True):
            ok = entail >= self.threshold
            supported += ok
            details.append({"claim": claim, "entailment": round(entail, 3), "contradiction": round(contra, 3),
                            "verdict": "supported" if ok else "contradicted" if contra > entail else "unsupported"})  # fmt: skip
        value = supported / len(claims)
        bad = [d["claim"] for d in details if d["verdict"] != "supported"]
        return [
            self.make(
                "factuality.nli_faithfulness",
                value,
                not bad,
                f"unsupported: {bad}" if bad else "",
                claims=details,
                nli_model=self.model_name,
            ),
            self.make("factuality.nli_hallucination_rate", 1.0 if bad else 0.0, not bad),
        ]
