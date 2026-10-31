"""Deterministic stand-in judges for DeepEval and Ragas.

They let the adapters be exercised with no API key. They verify the *wiring* (prompts flow to
the judge, structured replies parse, scores and costs land on the Score) - not the quality of
the framework's judgment, which needs a real judge model.

Ragas prompts embed their JSON schema and their input, so ``StubJudge`` answers the two
Faithfulness prompts with simple lexical logic (a statement is supported when every number in
it appears in the context). Anything else gets the smallest schema-valid reply.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ... import textutil
from ...models import Usage
from ..judge import Judge, JudgeCompletion, SimJudge
from .common import minimal_instance

_SCHEMA_RE = re.compile(r"JSON Schema:\s*(\{.*\})\s*Do not use single quotes", re.S)
_INPUT_RE = re.compile(
    r"Now perform the same with the following input\s*input:\s*(\{.*\})\s*Output:\s*$", re.S
)


def _lexically_supported(statement: str, context: str) -> bool:
    nums = set(textutil.numbers(statement))
    if nums and not nums <= set(textutil.numbers(context)):
        return False
    words = set(textutil.content_words(statement))
    return not words or len(words & set(textutil.content_words(context))) / len(words) >= 0.5


class StubJudge(SimJudge):
    """A SimJudge that can also answer free-form ``complete()`` calls (for adapter dry-runs)."""

    model = "sim/judge"

    async def complete(self, system: str, user: str, schema: dict[str, Any] | None = None) -> JudgeCompletion:
        text = json.dumps(self._reply(user, schema))
        return JudgeCompletion(text=text, model=self.model, usage=Usage(input_tokens=len(user) // 4, output_tokens=len(text) // 4),
                               cost_usd=0.0)  # fmt: skip

    def _reply(self, prompt: str, schema: dict[str, Any] | None) -> Any:
        embedded = _SCHEMA_RE.search(prompt)
        if embedded:
            schema = json.loads(embedded.group(1))
        if schema is None:
            return {}
        title = schema.get("title", "")
        given = _INPUT_RE.search(prompt)
        data = json.loads(given.group(1)) if given else {}
        if title == "StatementGeneratorOutput":
            return {"statements": textutil.sentences(str(data.get("answer", "")))}
        if title in ("NLIStatementOutput", "StatementFaithfulnessAnswers"):
            context = str(data.get("context", ""))
            statements = data.get("statements", [])
            return {"statements": [
                {"statement": s, "reason": "lexical check", "verdict": int(_lexically_supported(s, context))}
                for s in statements
            ]}  # fmt: skip
        return minimal_instance(schema)


def deepeval_stub_llm() -> Any:
    """A DeepEvalBaseLLM that answers every schema-constrained prompt with a minimal valid reply."""
    from deepeval.models import DeepEvalBaseLLM

    class Stub(DeepEvalBaseLLM):
        def __init__(self) -> None:
            super().__init__(model="stub/deepeval")

        def load_model(self, *args: Any, **kwargs: Any) -> Any:
            return self

        def get_model_name(self, *args: Any, **kwargs: Any) -> str:
            return "stub/deepeval"

        async def a_generate(self, prompt: str, schema: Any = None, *args: Any, **kwargs: Any) -> Any:
            if schema is None:
                return "{}"
            return schema.model_validate(minimal_instance(schema.model_json_schema()))

        def generate(self, prompt: str, schema: Any = None, *args: Any, **kwargs: Any) -> Any:
            from .common import run_coroutine_sync

            return run_coroutine_sync(self.a_generate(prompt, schema))

    return Stub()


def ragas_stub_llm(judge: Judge | None = None) -> Any:
    from .common import UsageSink
    from .ragas_adapter import make_platform_llm

    return make_platform_llm(judge or StubJudge(), UsageSink())
