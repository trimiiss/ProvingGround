"""DeepEval metrics (G-Eval, Faithfulness, Answer Relevancy, Hallucination, Bias) as scorers.

DeepEval talks to its judge through a ``DeepEvalBaseLLM``; ``PlatformLLM`` points that at the
platform's judge model, so DeepEval calls share ProvingGround's provider layer, response cache,
cost accounting and (for Claude) schema-constrained output.
"""

from __future__ import annotations

import os
from typing import Any

from ...models import Score, TargetOutput, TestCase
from ..base import ScoreContext, Scorer, register
from ..judge import Judge
from .common import UsageSink, extract_json, require, run_coroutine_sync

# DeepEval phones home by default; evaluation data must not leave the machine unasked.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")

#: kind -> (platform metric name, higher_is_better)
METRICS: dict[str, tuple[str, bool]] = {
    "faithfulness": ("factuality.deepeval_faithfulness", True),
    "answer_relevancy": ("quality.deepeval_answer_relevancy", True),
    "hallucination": ("factuality.deepeval_hallucination_rate", False),
    "bias": ("safety.deepeval_bias_rate", False),
    "geval": ("quality.deepeval_geval", True),
}


def make_platform_llm(judge: Judge, sink: UsageSink) -> Any:
    """Build a DeepEvalBaseLLM backed by a platform judge (imported lazily)."""
    require("deepeval")
    from deepeval.models import DeepEvalBaseLLM

    class PlatformLLM(DeepEvalBaseLLM):
        def __init__(self) -> None:
            self._judge = judge
            self._sink = sink
            super().__init__(model=judge.model)

        def load_model(self, *args: Any, **kwargs: Any) -> Any:
            return self

        def get_model_name(self, *args: Any, **kwargs: Any) -> str:
            return self._judge.model

        async def a_generate(self, prompt: str, schema: Any = None, *args: Any, **kwargs: Any) -> Any:
            json_schema = schema.model_json_schema() if schema is not None else None
            completion = await self._judge.complete(system="", user=prompt, schema=json_schema)
            self._sink.add(completion.usage, completion.cost_usd, completion.model)
            if schema is not None:
                return schema.model_validate(extract_json(completion.text))
            return completion.text

        def generate(self, prompt: str, schema: Any = None, *args: Any, **kwargs: Any) -> Any:
            return run_coroutine_sync(self.a_generate(prompt, schema))

    return PlatformLLM()


@register
class DeepEvalScorer(Scorer):
    """``- deepeval: {metric: faithfulness}`` or ``{metric: geval, name: tone, criteria: "..."}``."""

    name = "deepeval"
    uses_judge = True

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        require("deepeval")
        self.kind = str(params.get("metric", "faithfulness"))
        if self.kind not in METRICS:
            raise ValueError(f"unknown deepeval metric {self.kind!r} (known: {', '.join(METRICS)})")
        self.threshold = float(params.get("threshold", 0.5))
        self.criteria = str(params.get("criteria", ""))
        self.label = str(params.get("name", "custom"))
        self._llm: Any = params.get("llm")  # tests inject a stub DeepEvalBaseLLM
        self.requires = (
            frozenset({"context"}) if self.kind in ("faithfulness", "hallucination") else frozenset()
        )
        base, _ = METRICS[self.kind]
        self.metrics = (f"{base}_{self.label}" if self.kind == "geval" else base,)

    def _build_metric(self, llm: Any) -> Any:
        from deepeval import metrics as dm
        from deepeval.test_case import LLMTestCaseParams

        common = {"threshold": self.threshold, "model": llm, "async_mode": True, "include_reason": True}
        if self.kind == "faithfulness":
            return dm.FaithfulnessMetric(**common)
        if self.kind == "answer_relevancy":
            return dm.AnswerRelevancyMetric(**common)
        if self.kind == "hallucination":
            return dm.HallucinationMetric(**common)
        if self.kind == "bias":
            return dm.BiasMetric(**common)
        params = [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT]
        geval_args = {
            k: v for k, v in common.items() if k != "include_reason"
        }  # GEval always explains itself
        return dm.GEval(name=self.label, criteria=self.criteria, evaluation_params=params, **geval_args)

    async def score(self, case: TestCase, output: TargetOutput, ctx: ScoreContext) -> list[Score]:
        from deepeval.test_case import LLMTestCase

        sink = UsageSink()
        llm = self._llm or make_platform_llm(ctx.judge, sink)
        metric = self._build_metric(llm)
        docs = output.retrieved_context or case.context or []
        test_case = LLMTestCase(
            input=case.prompt_text, actual_output=output.text, expected_output=case.expected,
            context=docs or None, retrieval_context=docs or None,
        )  # fmt: skip
        await metric.a_measure(test_case, _show_indicator=False)
        value = float(metric.score if metric.score is not None else 0.0)
        passed = bool(metric.is_successful())
        out = self.make(self.metrics[0], value, passed, str(getattr(metric, "reason", "") or ""),
                        framework="deepeval", kind=self.kind, threshold=self.threshold)  # fmt: skip
        out.judge_model = sink.model or getattr(llm, "get_model_name", lambda: None)()
        out.judge_usage = sink.usage if sink.calls else None
        out.judge_cost_usd = sink.total_cost
        return [out]
