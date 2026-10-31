"""An HTTP endpoint as the target: the way to test another team's deployed agent.

``request`` is a JSON template where ``"{{name}}"`` placeholders are replaced by case values
(input, messages, context, case_id, metadata, tags). A string that is exactly one
placeholder is replaced by the raw value (so lists stay lists). ``response`` maps output
fields to dotted paths in the JSON reply, e.g. ``{"text": "choices.0.message.content"}``.
The eval's W3C ``traceparent`` is forwarded so the agent's own spans join the eval trace.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from ..config import TargetConfig
from ..models import CaseStatus, TargetInfo, TargetOutput, TestCase, ToolCall, Usage
from ..providers.pricing import cost_usd
from ..tracing import current_traceparent
from .base import InvokeContext, Target, TargetError

DEFAULT_REQUEST = {
    "input": "{{input}}",
    "messages": "{{messages}}",
    "context": "{{context}}",
    "case_id": "{{case_id}}",
}
DEFAULT_RESPONSE = {"text": "output"}
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_template(template: Any, values: dict[str, Any]) -> Any:
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template.strip())
        if whole:
            return values.get(whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), template)
    if isinstance(template, list):
        return [render_template(v, values) for v in template]
    if isinstance(template, dict):
        return {k: render_template(v, values) for k, v in template.items()}
    return template


def extract_path(data: Any, path: str) -> Any:
    """Dotted-path lookup with list indexes: ``a.b.0.c`` (a leading ``$.`` is allowed)."""
    current = data
    for part in path.removeprefix("$.").split("."):
        if part == "":
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


class HttpTarget(Target):
    def __init__(self, name: str, config: TargetConfig, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(name, config)
        self.client = client or httpx.AsyncClient(timeout=config.timeout_s or 120.0)

    @property
    def info(self) -> TargetInfo:
        return TargetInfo(
            name=self.name,
            kind="http",
            model=self.config.model,
            fingerprint=self._fingerprint(),
            description=self.config.description,
            canaries=self.config.canaries,
            forbidden_terms=self.config.forbidden_terms,
            tool_policies=self.config.tool_policies,
            config={"url": self.config.url, "public_info": self.config.args.get("public_info", [])},
            cacheable=True,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            k: _ENV.sub(lambda m: os.environ.get(m.group(1), ""), v) for k, v in self.config.headers.items()
        }
        traceparent = current_traceparent()
        if traceparent:
            headers["traceparent"] = traceparent
        return headers

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        values = {
            "input": case.prompt_text,
            "messages": [m.model_dump(mode="json", exclude_none=True) for m in case.conversation()],
            "context": case.context or [],
            "case_id": case.id,
            "metadata": case.metadata,
            "tags": case.tags,
        }
        body = render_template(self.config.request or DEFAULT_REQUEST, values)
        assert self.config.url
        started = time.perf_counter()
        try:
            resp = await self.client.request(
                self.config.method, self.config.url, json=body, headers=self._headers()
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TargetError(f"{self.name}: {type(exc).__name__}: {exc}", transient=True) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TargetError(f"{self.name}: HTTP {resp.status_code}", transient=True)
        if resp.status_code >= 400:
            raise TargetError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", transient=False)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TargetError(f"{self.name}: response is not JSON", transient=False) from exc
        return self._map(data, elapsed_ms)

    def _map(self, data: Any, elapsed_ms: float) -> TargetOutput:
        mapping = {**DEFAULT_RESPONSE, **self.config.response}

        def get(field: str) -> Any:
            path = mapping.get(field)
            return extract_path(data, path) if path else None

        usage = Usage(
            input_tokens=int(get("usage.input_tokens") or 0),
            output_tokens=int(get("usage.output_tokens") or 0),
            cache_read_tokens=int(get("usage.cache_read_tokens") or 0),
            cache_write_tokens=int(get("usage.cache_write_tokens") or 0),
        )
        raw_calls = get("tool_calls") or []
        tool_calls = [
            ToolCall(id=str(c.get("id", i)), name=str(c.get("name")), arguments=c.get("arguments") or {})
            for i, c in enumerate(raw_calls)
            if isinstance(c, dict) and c.get("name")
        ]
        status_value = str(get("status") or "ok").lower()
        status = CaseStatus(status_value) if status_value in CaseStatus._value2member_map_ else CaseStatus.OK
        model_served = get("model")
        reported_cost = get("cost_usd")
        model_ref = self.config.model
        latency = get("latency_ms")
        return TargetOutput(
            status=status,
            text=str(get("text") or ""),
            tool_calls=tool_calls,
            retrieved_context=[str(c) for c in (get("retrieved_context") or [])],
            usage=usage,
            model_requested=model_ref,
            model_served=str(model_served) if model_served else None,
            latency_ms=float(latency) if latency is not None else elapsed_ms,
            cost_usd=float(reported_cost) if reported_cost is not None else cost_usd(model_ref, usage),
            trace_id=get("trace_id"),
            raw={"http_elapsed_ms": elapsed_ms},
        )

    async def aclose(self) -> None:
        await self.client.aclose()
