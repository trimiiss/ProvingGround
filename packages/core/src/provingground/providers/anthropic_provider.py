"""Claude via the official ``anthropic`` SDK (1.x).

Notes that matter for evaluation:

* No ``temperature`` / ``top_p`` / ``top_k``: API version 2026-10-21 rejects sampling params.
* ``stop_reason == "refusal"`` maps to ``refused`` - in red-team scoring that is a defense.
* Usage keeps cache reads/writes separate so cost is priced at cache rates.
* Server-side fallbacks are only for the judge. A target under test must be served by exactly
  the configured model, so targets never enable them; the runner flags any mismatch.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from ..models import Message, ToolCall, Usage
from .base import ChatRequest, ChatResponse, Provider, ProviderError, StopReason

FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 16000

_STOP: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "refusal": "refusal",
    "pause_turn": "other",
}

_TRANSIENT = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert to Messages API shape; consecutive tool results merge into one user turn."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue  # top-level `system` carries operator instructions
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(b.get("type") == "tool_result" for b in out[-1]["content"])
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if m.role == "assistant":
            if m.provider_content:
                out.append({"role": "assistant", "content": m.provider_content})
                continue
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks or m.content})
            continue
        out.append({"role": "user", "content": m.content})
    return out


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 600.0,
        http_client: Any = None,
        fallbacks: bool = False,
        cache_system_prompt: bool = True,
    ) -> None:
        self.fallbacks = fallbacks
        self.cache_system_prompt = cache_system_prompt
        # max_retries=0: the runner retries, so latency covers the final attempt only.
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout_s, http_client=http_client
        )

    def build_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": to_anthropic_messages(request.messages),
        }
        if request.system:
            block: dict[str, Any] = {"type": "text", "text": request.system}
            if self.cache_system_prompt:
                block["cache_control"] = {"type": "ephemeral"}
            kwargs["system"] = [block]
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if request.response_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.response_schema}
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    async def chat(self, request: ChatRequest) -> ChatResponse:
        kwargs = self.build_kwargs(request)
        started = time.perf_counter()
        try:
            if self.fallbacks:
                resp: Any = await self.client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                resp = await self.client.messages.create(**kwargs)
        except _TRANSIENT as exc:
            raise ProviderError(
                f"anthropic: {exc}", transient=True, status_code=getattr(exc, "status_code", None)
            ) from exc
        except anthropic.APIStatusError as exc:
            transient = exc.status_code in (408, 409, 429, 529) or exc.status_code >= 500
            raise ProviderError(f"anthropic: HTTP {exc.status_code}: {exc.message}", transient=transient,
                                status_code=exc.status_code) from exc  # fmt: skip
        latency_ms = (time.perf_counter() - started) * 1000

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        blocks: list[dict[str, Any]] = []
        for block in resp.content:
            blocks.append(block.model_dump(mode="json", exclude_none=True))
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(json.dumps(block.input))
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(args)))
        u = resp.usage
        usage = Usage(
            input_tokens=int(u.input_tokens or 0),
            output_tokens=int(u.output_tokens or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )
        raw: dict[str, Any] = {"id": resp.id, "stop_reason": resp.stop_reason}
        details = getattr(resp, "stop_details", None)
        if details is not None:
            raw["stop_details"] = (
                details.model_dump(mode="json") if hasattr(details, "model_dump") else str(details)
            )
        iterations = getattr(u, "iterations", None) or []
        if any(getattr(i, "type", "") == "fallback_message" for i in iterations):
            raw["fallback_ran"] = True
        return ChatResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            model_served=resp.model,
            stop_reason=_STOP.get(resp.stop_reason or "", "other"),
            latency_ms=latency_ms,
            provider_content=blocks,
            raw=raw,
        )

    async def aclose(self) -> None:
        await self.client.close()
