"""OpenTelemetry setup plus GenAI semantic-convention helpers.

Agents instrumented with these helpers emit spans that the runner captures in-process (for
python targets) or that reach the platform over OTLP (for HTTP targets, which receive the
eval's ``traceparent``). Attribute names follow the OpenTelemetry GenAI conventions
(``gen_ai.*``); ProvingGround-specific attributes use the ``pground.*`` prefix.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .models import SpanRecord, Usage

GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_PROVIDER = "gen_ai.provider.name"
GEN_AI_SYSTEM = "gen_ai.system"  # older name, kept for backends that still read it
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"

MAX_CONTENT_CHARS = 4000
_TRACE_CONTENT = os.environ.get("PGROUND_TRACE_CONTENT", "1") not in ("0", "false", "no")


def span_to_record(span: ReadableSpan) -> SpanRecord:
    ctx = span.context
    parent = span.parent
    resource_attrs = dict(span.resource.attributes) if span.resource else {}
    return SpanRecord(
        trace_id=format(ctx.trace_id, "032x") if ctx else "",
        span_id=format(ctx.span_id, "016x") if ctx else "",
        parent_span_id=format(parent.span_id, "016x") if parent else None,
        name=span.name,
        kind=span.kind.name.lower(),
        start_time_ns=span.start_time or 0,
        end_time_ns=span.end_time or span.start_time or 0,
        status=span.status.status_code.name.lower(),
        attributes={k: _plain(v) for k, v in (span.attributes or {}).items()},
        events=[
            {
                "name": e.name,
                "time_ns": e.timestamp,
                "attributes": {k: _plain(v) for k, v in (e.attributes or {}).items()},
            }
            for e in span.events
        ],
        service_name=str(resource_attrs.get("service.name")) if resource_attrs.get("service.name") else None,
    )


def _plain(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


class SpanCollector(SpanProcessor):
    """Keeps finished spans grouped by trace id so the runner can attach them to results."""

    def __init__(self, max_traces: int = 20_000) -> None:
        self._traces: OrderedDict[str, list[SpanRecord]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_traces

    def on_start(self, span: Span, parent_context: Any = None) -> None:  # pragma: no cover - no-op
        return None

    def on_end(self, span: ReadableSpan) -> None:
        if span.name == "pground.case":
            return
        record = span_to_record(span)
        with self._lock:
            self._traces.setdefault(record.trace_id, []).append(record)
            self._traces.move_to_end(record.trace_id)
            while len(self._traces) > self._max:
                self._traces.popitem(last=False)

    def pop(self, trace_id: str) -> list[SpanRecord]:
        with self._lock:
            spans = self._traces.pop(trace_id, [])
        return sorted(spans, key=lambda s: s.start_time_ns)

    def shutdown(self) -> None:
        with self._lock:
            self._traces.clear()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


collector = SpanCollector()
_configured = False
_lock = threading.Lock()


def setup_tracing(
    service_name: str = "provingground",
    *,
    otlp_endpoint: str | None = None,
    otlp_headers: dict[str, str] | None = None,
) -> TracerProvider:
    """Idempotently install a tracer provider with the in-process collector (+ optional OTLP)."""
    global _configured
    with _lock:
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            trace.set_tracer_provider(provider)
            provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        if not _configured:
            provider.add_span_processor(collector)
            endpoint = otlp_endpoint or os.environ.get("PGROUND_OTLP_ENDPOINT")
            if endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                headers = dict(otlp_headers or {})
                key = os.environ.get("PGROUND_INGEST_KEY")
                if key and "x-api-key" not in headers:
                    headers["x-api-key"] = key
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
                )
            _configured = True
        return provider


def get_tracer(name: str = "provingground") -> trace.Tracer:
    return trace.get_tracer(name)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CONTENT_CHARS else text[:MAX_CONTENT_CHARS] + "…"


@contextmanager
def llm_span(
    provider: str,
    model: str,
    *,
    operation: str = "chat",
    input_text: str | None = None,
    tracer_name: str = "provingground",
) -> Iterator[Span]:
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(f"{operation} {model}", kind=SpanKind.CLIENT) as span:
        span.set_attribute(GEN_AI_OPERATION, operation)
        span.set_attribute(GEN_AI_PROVIDER, provider)
        span.set_attribute(GEN_AI_SYSTEM, provider)
        span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        if input_text and _TRACE_CONTENT:
            span.set_attribute("pground.input", _clip(input_text))
        yield span


def record_llm_result(
    span: Span,
    *,
    response_model: str | None,
    usage: Usage,
    cost_usd: float | None,
    stop_reason: str | None,
    output_text: str | None = None,
    latency_ms: float | None = None,
) -> None:
    if response_model:
        span.set_attribute(GEN_AI_RESPONSE_MODEL, response_model)
    span.set_attribute(GEN_AI_INPUT_TOKENS, usage.total_input_tokens)
    span.set_attribute(GEN_AI_OUTPUT_TOKENS, usage.output_tokens)
    span.set_attribute("pground.usage.cache_read_tokens", usage.cache_read_tokens)
    span.set_attribute("pground.usage.cache_write_tokens", usage.cache_write_tokens)
    if cost_usd is not None:
        span.set_attribute("pground.cost_usd", cost_usd)
    if stop_reason:
        span.set_attribute(GEN_AI_FINISH_REASONS, [stop_reason])
    if latency_ms is not None:
        span.set_attribute("pground.latency_ms", latency_ms)
    if output_text and _TRACE_CONTENT:
        span.set_attribute("pground.output", _clip(output_text))


def record_io(
    span: Span,
    *,
    input_text: str | None = None,
    output_text: str | None = None,
    docs: list[str] | None = None,
) -> None:
    """Attach the user input / answer / retrieved documents to a span (when content tracing is on).

    These attributes power the trace explorer and the "add production incident to a dataset" flow.
    Set ``PGROUND_TRACE_CONTENT=0`` to keep prompts and answers out of traces entirely.
    """
    if not _TRACE_CONTENT:
        return
    if input_text is not None:
        span.set_attribute("pground.input", _clip(input_text))
    if output_text is not None:
        span.set_attribute("pground.output", _clip(output_text))
    if docs is not None:
        span.set_attribute("pground.retrieval.docs", [_clip(d) for d in docs[:8]])


@contextmanager
def tool_span(name: str, call_id: str | None = None, arguments: Any = None) -> Iterator[Span]:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"execute_tool {name}", kind=SpanKind.INTERNAL) as span:
        span.set_attribute(GEN_AI_OPERATION, "execute_tool")
        span.set_attribute(GEN_AI_TOOL_NAME, name)
        if call_id:
            span.set_attribute(GEN_AI_TOOL_CALL_ID, call_id)
        if arguments is not None and _TRACE_CONTENT:
            import json

            span.set_attribute("pground.tool.arguments", _clip(json.dumps(arguments, default=str)))
        yield span


@contextmanager
def agent_span(
    agent: str, *, kind: SpanKind = SpanKind.SERVER, attributes: dict[str, Any] | None = None
) -> Iterator[Span]:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"invoke_agent {agent}", kind=kind) as span:
        span.set_attribute(GEN_AI_OPERATION, "invoke_agent")
        span.set_attribute(GEN_AI_AGENT_NAME, agent)
        for k, v in (attributes or {}).items():
            if v is not None:
                span.set_attribute(k, v)
        yield span
