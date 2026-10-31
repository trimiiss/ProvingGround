"""Ragas RAG metrics (Faithfulness, Response Relevancy, Factual Correctness, Context
Precision/Recall) as scorers, with Ragas' judge calls routed through the platform judge."""

from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Any

from ...models import Score, TargetOutput, TestCase
from ..base import ScoreContext, Scorer, register
from ..judge import Judge
from .common import UsageSink, require

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

#: kind -> (platform metric name, needs context, needs reference)
METRICS: dict[str, tuple[str, bool, bool]] = {
    "faithfulness": ("factuality.ragas_faithfulness", True, False),
    "response_relevancy": ("quality.ragas_response_relevancy", False, False),
    "factual_correctness": ("quality.ragas_factual_correctness", False, True),
    "context_precision": ("quality.ragas_context_precision", True, True),
    "context_recall": ("quality.ragas_context_recall", True, True),
}


def apply_langchain_compat() -> None:
    """Ragas 0.4 imports ``langchain_community`` Vertex classes that langchain-community 0.4 removed.

    Ragas only uses them in ``isinstance`` allow-lists, so inert stand-ins are enough. Applied
    here (not globally) so the incompatibility cannot leak into the rest of the platform.
    """
    try:
        importlib.import_module("langchain_community.chat_models.vertexai")
    except ModuleNotFoundError:
        stub = types.ModuleType("langchain_community.chat_models.vertexai")
        stub.__dict__["ChatVertexAI"] = type("ChatVertexAI", (), {})
        sys.modules[stub.__name__] = stub
    llms = importlib.import_module("langchain_community.llms")
    try:
        llms.__getattr__("VertexAI")  # lazy attribute: raises if the optional integration is gone
    except Exception:
        llms.__dict__["VertexAI"] = type("VertexAI", (), {})


def make_platform_llm(judge: Judge, sink: UsageSink) -> Any:
    require("ragas")
    apply_langchain_compat()
    from langchain_core.outputs import Generation, LLMResult
    from ragas.llms.base import BaseRagasLLM

    class PlatformRagasLLM(BaseRagasLLM):
        def __init__(self) -> None:
            super().__init__()
            self._judge = judge
            self._sink = sink

        def is_finished(self, response: Any) -> bool:
            return True

        async def agenerate_text(self, prompt: Any, n: int = 1, temperature: float | None = 0.01,
                                 stop: list[str] | None = None, callbacks: Any = None) -> Any:  # fmt: skip
            completion = await self._judge.complete(system="", user=prompt.to_string())
            self._sink.add(completion.usage, completion.cost_usd, completion.model)
            return LLMResult(generations=[[Generation(text=completion.text)]])

        def generate_text(self, prompt: Any, n: int = 1, temperature: float = 0.01,
                          stop: list[str] | None = None, callbacks: Any = None) -> Any:  # fmt: skip
            from .common import run_coroutine_sync

            return run_coroutine_sync(self.agenerate_text(prompt, n, temperature, stop, callbacks))

    return PlatformRagasLLM()


@register
class RagasScorer(Scorer):
    """``- ragas: {metric: faithfulness}``."""

    name = "ragas"
    uses_judge = True

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        require("ragas")
        self.kind = str(params.get("metric", "faithfulness"))
        if self.kind not in METRICS:
            raise ValueError(f"unknown ragas metric {self.kind!r} (known: {', '.join(METRICS)})")
        self.threshold = float(params.get("threshold", 0.5))
        self._llm: Any = params.get("llm")  # tests inject a stub BaseRagasLLM
        metric_name, needs_context, needs_reference = METRICS[self.kind]
        self.metrics = (metric_name,)
        required = set()
        if needs_context:
            required.add("context")
        if needs_reference:
            required.add("expected")
        self.requires = frozenset(required)

    def _build_metric(self, llm: Any) -> Any:
        apply_langchain_compat()
        from ragas import metrics as rm

        return {
            "faithfulness": rm.Faithfulness,
            "response_relevancy": rm.ResponseRelevancy,
            "factual_correctness": rm.FactualCorrectness,
            "context_precision": rm.LLMContextPrecisionWithReference,
            "context_recall": rm.LLMContextRecall,
        }[self.kind](llm=llm)

    async def score(self, case: TestCase, output: TargetOutput, ctx: ScoreContext) -> list[Score]:
        apply_langchain_compat()
        from ragas.dataset_schema import SingleTurnSample

        sink = UsageSink()
        llm = self._llm or make_platform_llm(ctx.judge, sink)
        metric = self._build_metric(llm)
        sample = SingleTurnSample(
            user_input=case.prompt_text, response=output.text, reference=case.expected,
            retrieved_contexts=list(output.retrieved_context or case.context or []),
        )  # fmt: skip
        value = float(await metric.single_turn_ascore(sample))
        out = self.make(self.metrics[0], value, value >= self.threshold, "", framework="ragas", kind=self.kind,
                        threshold=self.threshold)  # fmt: skip
        out.judge_model = sink.model or ctx.judge.model
        out.judge_usage = sink.usage if sink.calls else None
        out.judge_cost_usd = sink.total_cost
        return [out]
