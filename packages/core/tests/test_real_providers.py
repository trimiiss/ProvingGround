"""The real providers against mocked HTTP: assert on the wire format both ways."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from provingground.config import TargetConfig
from provingground.models import CaseStatus, Message, TestCase, ToolSpec
from provingground.providers import ProviderRegistry
from provingground.providers.anthropic_provider import FALLBACK_BETA, AnthropicProvider, to_anthropic_messages
from provingground.providers.base import ChatRequest, ProviderError
from provingground.providers.openai_compat import OpenAICompatProvider
from provingground.providers.pricing import cost_usd
from provingground.targets import InvokeContext, build_target


def anthropic_reply(**over: Any) -> dict[str, Any]:
    base = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": "Footwear can be returned within 30 days."}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 18, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 50},
    }  # fmt: skip
    return {**base, **over}


class Recorder:
    def __init__(self, reply: Any, status: int = 200) -> None:
        self.reply, self.status = reply, status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.reply)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def anthropic_with(reply: Any, status: int = 200, **kw: Any) -> tuple[AnthropicProvider, Recorder]:
    rec = Recorder(reply, status)
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    return AnthropicProvider(api_key="test-key", http_client=client, **kw), rec


REQ = ChatRequest(model="claude-opus-5-5", system="Be brief.", messages=[Message(role="user", content="Return window?")],
                  max_tokens=500, effort="low", temperature=0.2)  # fmt: skip


async def test_anthropic_request_shape_and_usage_parsing() -> None:
    provider, rec = anthropic_with(anthropic_reply())
    resp = await provider.chat(REQ)
    body = rec.body
    assert "temperature" not in body and "top_p" not in body  # sampling params are rejected by the API
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 500
    assert body["output_config"] == {"effort": "low"}
    assert body["system"] == [{"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}]
    assert rec.requests[-1].url.path == "/v1/messages"
    assert resp.text.startswith("Footwear") and resp.model_served == "claude-opus-5-5"
    u = resp.usage
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens) == (120, 18, 800, 50)
    # Priced at cache rates: 120*4 + 18*20 + 800*0.20 + 50*5 (1.25x of input) per 1M tokens
    expected = (120 * 4.0 + 18 * 20.0 + 800 * 0.20 + 50 * 5.0) / 1e6
    assert cost_usd("anthropic/claude-opus-5-5", u) == pytest.approx(expected)


async def test_anthropic_structured_output_and_tools() -> None:
    schema = {
        "type": "object",
        "properties": {"score": {"type": "integer"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    provider, rec = anthropic_with(anthropic_reply())
    await provider.chat(ChatRequest(model="claude-opus-5-5", messages=[Message(role="user", content="x")], response_schema=schema,
                                    tools=[ToolSpec(name="lookup_order", description="d", parameters={"type": "object"})]))  # fmt: skip
    body = rec.body
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert body["tools"][0] == {
        "name": "lookup_order",
        "description": "d",
        "input_schema": {"type": "object"},
    }
    assert "system" not in body  # none supplied


async def test_anthropic_tool_use_round_trip() -> None:
    reply = anthropic_reply(
        content=[{"type": "text", "text": "Looking that up."}, {"type": "tool_use", "id": "toolu_1", "name": "lookup_order", "input": {"order_id": "ORD-1"}}],
        stop_reason="tool_use",
    )  # fmt: skip
    provider, _ = anthropic_with(reply)
    resp = await provider.chat(REQ)
    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls[0].name == "lookup_order" and resp.tool_calls[0].arguments == {"order_id": "ORD-1"}
    # The assistant turn and the tool result convert back into Messages API shape (results merge into one user turn).
    convo = [
        Message(role="user", content="status of ORD-1?"),
        resp.as_assistant_message(),
        Message(role="tool", content='{"status":"shipped"}', tool_call_id="toolu_1"),
        Message(role="tool", content='{"ok":true}', tool_call_id="toolu_2"),
    ]
    wire = to_anthropic_messages(convo)
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in wire[2]["content"]] == ["toolu_1", "toolu_2"]


async def test_anthropic_refusal_maps_to_refused_status() -> None:
    reply = anthropic_reply(
        content=[],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "x"},
    )
    registry = ProviderRegistry()
    provider, _ = anthropic_with(reply)
    registry._cache[("anthropic", False)] = provider
    target = build_target(
        "t", TargetConfig(kind="model", model="anthropic/claude-opus-5-5"), registry=registry
    )
    out = await target.invoke(TestCase(id="c", input="hi"), InvokeContext(run_id="r", suite="s"))
    assert out.status == CaseStatus.REFUSED  # a provider refusal is a successful defense, not an error
    assert out.raw["stop_details"]["category"] == "cyber"


async def test_fallbacks_are_only_enabled_for_the_judge() -> None:
    target_provider, target_rec = anthropic_with(anthropic_reply(), fallbacks=False)
    await target_provider.chat(REQ)
    assert target_rec.requests[-1].url.path == "/v1/messages" and "fallbacks" not in target_rec.body

    judge_provider, judge_rec = anthropic_with(anthropic_reply(), fallbacks=True)
    await judge_provider.chat(REQ)
    assert judge_rec.body["fallbacks"] == "default"
    assert FALLBACK_BETA in judge_rec.requests[-1].headers["anthropic-beta"]


async def test_registry_builds_judge_and_target_clients_separately() -> None:
    registry = ProviderRegistry()
    assert registry.get("anthropic", judge=True) is not registry.get("anthropic", judge=False)
    assert registry.get("anthropic", judge=True).fallbacks and not registry.get("anthropic").fallbacks  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("status", "transient"), [(429, True), (529, True), (500, True), (400, False), (401, False)]
)
async def test_anthropic_error_classification(status: int, transient: bool) -> None:
    err = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    provider, _ = anthropic_with(err, status=status)
    with pytest.raises(ProviderError) as exc:
        await provider.chat(REQ)
    assert exc.value.transient is transient and exc.value.status_code == status


# --- OpenAI-compatible ------------------------------------------------------------------------


def openai_reply(**message: Any) -> dict[str, Any]:
    msg = {"role": "assistant", "content": "30 days.", **message}
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-5-mini-2026-01-01",
        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 9, "total_tokens": 209, "prompt_tokens_details": {"cached_tokens": 150}},
    }  # fmt: skip


def openai_with(reply: Any, status: int = 200, **kw: Any) -> tuple[OpenAICompatProvider, Recorder]:
    rec = Recorder(reply, status)
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    return OpenAICompatProvider(
        "openai", base_url="https://api.test/v1", api_key="k", http_client=client, **kw
    ), rec


async def test_openai_request_and_cached_token_split() -> None:
    provider, rec = openai_with(openai_reply(), max_tokens_param="max_completion_tokens")
    resp = await provider.chat(
        ChatRequest(
            model="gpt-5-mini", system="sys", messages=[Message(role="user", content="q")], max_tokens=300
        )
    )
    body = rec.body
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["max_completion_tokens"] == 300 and "max_tokens" not in body
    assert (
        resp.usage.input_tokens == 50 and resp.usage.cache_read_tokens == 150
    )  # cached tokens split out of the prompt
    assert resp.model_served == "gpt-5-mini-2026-01-01"
    price = cost_usd("openai/gpt-5-mini", resp.usage)
    assert price == pytest.approx((50 * 0.25 + 150 * 0.025 + 9 * 2.0) / 1e6)


async def test_openai_tool_calls_and_structured_output() -> None:
    reply = openai_reply(
        content=None,
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "lookup_order", "arguments": '{"order_id":"ORD-1"}'},
            }
        ],
    )
    provider, rec = openai_with(reply)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    resp = await provider.chat(ChatRequest(model="gpt-5-mini", messages=[Message(role="user", content="q")], response_schema=schema,
                                           tools=[ToolSpec(name="lookup_order", description="d")]))  # fmt: skip
    assert resp.stop_reason == "tool_use" and resp.tool_calls[0].arguments == {"order_id": "ORD-1"}
    assert rec.body["response_format"]["json_schema"]["schema"] == schema
    assert rec.body["tools"][0]["function"]["name"] == "lookup_order"


async def test_openai_refusal_and_errors() -> None:
    provider, _ = openai_with(openai_reply(content=None, refusal="I can't help with that."))
    resp = await provider.chat(ChatRequest(model="gpt-5-mini", messages=[Message(role="user", content="q")]))
    assert resp.stop_reason == "refusal" and resp.text == "I can't help with that."
    for status, transient in ((429, True), (503, True), (400, False)):
        bad, _ = openai_with({"error": {"message": "x", "type": "e"}}, status=status)
        with pytest.raises(ProviderError) as exc:
            await bad.chat(ChatRequest(model="m", messages=[Message(role="user", content="q")]))
        assert exc.value.transient is transient
