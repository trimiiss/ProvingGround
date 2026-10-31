"""OpenAI-compatible chat completions: OpenAI, OpenRouter, Groq, Gemini's OpenAI endpoint, Ollama, vLLM."""

from __future__ import annotations

import json
import time
from typing import Any

import openai

from ..models import Message, ToolCall, Usage
from .base import ChatRequest, ChatResponse, Provider, ProviderError, StopReason

_FINISH: dict[str, StopReason] = {
    "stop": "end",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "content_filter",
}


def _to_openai_messages(request: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if request.system:
        out.append({"role": "system", "content": request.system})
    for m in request.messages:
        out.append(_message(m))
    return out


def _message(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


class OpenAICompatProvider(Provider):
    def __init__(
        self,
        name: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens_param: str = "max_tokens",
        timeout_s: float = 120.0,
        http_client: Any = None,
    ) -> None:
        self.name = name
        self.max_tokens_param = max_tokens_param
        # Retries are owned by the runner so latency is measured on the final attempt only.
        self.client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            max_retries=0,
            timeout=timeout_s,
            http_client=http_client,
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        kwargs: dict[str, Any] = {"model": request.model, "messages": _to_openai_messages(request)}
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        if request.max_tokens:
            kwargs[self.max_tokens_param] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.response_schema},
            }
        started = time.perf_counter()
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ProviderError(f"{self.name}: {exc}", transient=True) from exc
        except openai.APIStatusError as exc:
            transient = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            raise ProviderError(f"{self.name}: HTTP {exc.status_code}: {exc.message}", transient=transient,
                                status_code=exc.status_code) from exc  # fmt: skip
        latency_ms = (time.perf_counter() - started) * 1000
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.arguments}
            tool_calls.append(
                ToolCall(
                    id=tc.id, name=fn.name, arguments=args if isinstance(args, dict) else {"value": args}
                )
            )
        stop: StopReason = _FINISH.get(choice.finish_reason or "", "other")
        if getattr(msg, "refusal", None):
            stop = "refusal"
        usage = Usage()
        if resp.usage is not None:
            details = getattr(resp.usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
            usage = Usage(
                input_tokens=max(0, int(resp.usage.prompt_tokens or 0) - cached),
                output_tokens=int(resp.usage.completion_tokens or 0),
                cache_read_tokens=cached,
            )
        return ChatResponse(
            text=msg.content or (msg.refusal if getattr(msg, "refusal", None) else "") or "",
            tool_calls=tool_calls,
            usage=usage,
            model_served=resp.model or request.model,
            stop_reason=stop,
            latency_ms=latency_ms,
            raw={"id": resp.id, "finish_reason": choice.finish_reason},
        )

    async def aclose(self) -> None:
        await self.client.close()
