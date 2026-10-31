"""A raw model as the target (provider + model + optional system prompt)."""

from __future__ import annotations

from ..config import TargetConfig
from ..models import CaseStatus, Message, TargetInfo, TargetOutput, TestCase
from ..providers import ProviderRegistry, default_registry, parse_model_ref
from ..providers.base import ChatRequest, ChatResponse
from ..providers.pricing import cost_usd
from ..tracing import llm_span, record_llm_result
from .base import InvokeContext, Target


def format_context(docs: list[str], question: str) -> str:
    """The prompt convention shared by ModelTarget and the demo agents."""
    body = "\n".join(f'<doc id="doc{i}">{doc}</doc>' for i, doc in enumerate(docs))
    return f"<context>\n{body}\n</context>\n\nQuestion: {question}"


def status_for(resp: ChatResponse) -> CaseStatus:
    if resp.stop_reason in ("refusal", "content_filter"):
        return CaseStatus.REFUSED
    if resp.stop_reason == "max_tokens":
        return CaseStatus.TRUNCATED
    return CaseStatus.OK


class ModelTarget(Target):
    def __init__(self, name: str, config: TargetConfig, registry: ProviderRegistry | None = None) -> None:
        super().__init__(name, config)
        assert config.model
        self.model_ref = config.model
        self.provider_name, self.model = parse_model_ref(config.model)
        self.registry = registry or default_registry()

    @property
    def info(self) -> TargetInfo:
        return TargetInfo(
            name=self.name,
            kind="model",
            model=self.model_ref,
            fingerprint=self._fingerprint(),
            description=self.config.description,
            canaries=self.config.canaries,
            forbidden_terms=self.config.forbidden_terms,
            tool_policies=self.config.tool_policies,
            cacheable=self.provider_name != "sim",
        )

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        provider = self.registry.get(self.provider_name)
        messages = case.conversation()
        if case.context:
            last = messages[-1]
            messages[-1] = Message(role=last.role, content=format_context(case.context, last.content))
        request = ChatRequest(
            model=self.model,
            messages=messages,
            system=self.config.system_prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            effort=self.config.effort,
            metadata={"sample": ctx.repeat},
        )
        with llm_span(self.provider_name, self.model, input_text=messages[-1].content) as span:
            resp = await provider.chat(request)
            cost = cost_usd(self.model_ref, resp.usage)
            record_llm_result(
                span, response_model=resp.model_served, usage=resp.usage, cost_usd=cost,
                stop_reason=resp.stop_reason, output_text=resp.text, latency_ms=resp.latency_ms,
            )  # fmt: skip
        return TargetOutput(
            status=status_for(resp),
            text=resp.text,
            messages=[*messages, resp.as_assistant_message()],
            tool_calls=resp.tool_calls,
            retrieved_context=list(case.context or []),
            usage=resp.usage,
            model_requested=self.model_ref,
            model_served=resp.model_served,
            stop_reason=resp.stop_reason,
            latency_ms=resp.latency_ms,
            cost_usd=cost,
            raw=resp.raw,
        )
