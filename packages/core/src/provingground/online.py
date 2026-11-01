"""Online evaluation: score live production requests with reference-free checks.

A live request has no expected answer, so only checks that need none apply:

* **faithfulness / hallucination** - claims in the answer are checked against the documents the
  agent actually retrieved (recorded on the trace);
* **canary leak** - the agent's secret markers must never appear in an answer;
* **PII leak** - personal data that the user did not supply must not appear in an answer;
* **refusal** - how often real users get refused (a rise usually means over-blocking).

The worker samples a share of recent traces and stores these scores; Prometheus exposes their
hourly means and alerts on drift.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import textutil
from .models import Score, TargetInfo, TargetOutput, TestCase
from .scorers import ScoreContext, build_scorer
from .scorers.judge import Judge

ONLINE_SCORERS = ("faithfulness", "canary_leak", "pii_leak")
REFUSAL_METRIC = "quality.refusal_rate"


@dataclass
class LiveRequest:
    """What a trace tells us about one production request."""

    trace_id: str
    agent: str
    question: str
    answer: str
    docs: list[str] = field(default_factory=list)
    error: bool = False


def sampled(trace_id: str, rate: float) -> bool:
    """Deterministic sampling: the same trace is always in or out, whichever worker looks at it."""
    if rate >= 1.0:
        return True
    bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


async def score_request(request: LiveRequest, target: TargetInfo, judge: Judge) -> list[Score]:
    """Reference-free scores for one live request. Errored requests are not scored (they are
    already counted by the error-rate metrics)."""
    if request.error or not request.answer.strip():
        return []
    case = TestCase(
        id=f"online-{request.trace_id[:12]}", input=request.question, context=request.docs or None
    )
    output = TargetOutput(text=request.answer, retrieved_context=request.docs)
    ctx = ScoreContext(target=target, judge=judge, suite="online")
    scores: list[Score] = []
    for name in ONLINE_SCORERS:
        scorer = build_scorer({"name": name})
        if scorer.applies(case, output):
            scores.extend(await scorer.score(case, output, ctx))
    refused = textutil.is_refusal(request.answer)
    scores.append(
        Score(
            metric=REFUSAL_METRIC,
            value=1.0 if refused else 0.0,
            passed=None,  # a refusal of a live request is not wrong in itself; the rate is what matters
            reason="refused" if refused else "",
            scorer="refusal",
        )
    )
    return scores
