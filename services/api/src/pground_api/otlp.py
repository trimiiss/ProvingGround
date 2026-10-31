"""OTLP/HTTP trace ingest: decode (protobuf or JSON) into spans enriched for the platform.

Spans follow the OpenTelemetry GenAI conventions (``gen_ai.*``) plus ``pground.*`` attributes.
Each stored span is denormalized with agent, operation, model, tokens and cost so dashboards
and Prometheus metrics need no JSON digging.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue

from provingground.models import SpanRecord, Usage
from provingground.providers.pricing import cost_usd

_KINDS = {0: "unspecified", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}
_STATUS = {0: "unset", 1: "ok", 2: "error"}


def _any_value(v: AnyValue) -> Any:
    kind = v.WhichOneof("value")
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return v.bool_value
    if kind == "int_value":
        return v.int_value
    if kind == "double_value":
        return v.double_value
    if kind == "array_value":
        return [_any_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in v.kvlist_value.values}
    return None


def _attrs(items: Any) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in items}


def parse_protobuf(body: bytes) -> list[SpanRecord]:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    spans: list[SpanRecord] = []
    for rs in request.resource_spans:
        resource = _attrs(rs.resource.attributes)
        service = resource.get("service.name")
        for ss in rs.scope_spans:
            for sp in ss.spans:
                spans.append(
                    SpanRecord(
                        trace_id=sp.trace_id.hex(),
                        span_id=sp.span_id.hex(),
                        parent_span_id=sp.parent_span_id.hex() or None,
                        name=sp.name,
                        kind=_KINDS.get(sp.kind, "internal"),
                        start_time_ns=sp.start_time_unix_nano,
                        end_time_ns=sp.end_time_unix_nano or sp.start_time_unix_nano,
                        status=_STATUS.get(sp.status.code, "unset"),
                        attributes=_attrs(sp.attributes),
                        events=[
                            {"name": e.name, "time_ns": e.time_unix_nano, "attributes": _attrs(e.attributes)}
                            for e in sp.events
                        ],
                        service_name=str(service) if service else None,
                    )
                )
    return spans


def _json_value(v: dict[str, Any]) -> Any:
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [_json_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _json_value(kv["value"]) for kv in v["kvlistValue"].get("values", [])}
    return None


def _json_attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {kv["key"]: _json_value(kv.get("value", {})) for kv in items or []}


def parse_json(body: bytes) -> list[SpanRecord]:
    """OTLP/JSON: trace/span ids are hex strings, timestamps are decimal strings."""
    doc = json.loads(body)
    spans: list[SpanRecord] = []
    for rs in doc.get("resourceSpans", []):
        service = _json_attrs(rs.get("resource", {}).get("attributes")).get("service.name")
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                status = sp.get("status", {}).get("code", 0)
                if isinstance(status, str):
                    status = {"STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}.get(status, 0)
                kind = sp.get("kind", 1)
                if isinstance(kind, str):
                    kind = {"SPAN_KIND_SERVER": 2, "SPAN_KIND_CLIENT": 3}.get(kind, 1)
                start = int(sp.get("startTimeUnixNano", 0))
                spans.append(
                    SpanRecord(
                        trace_id=sp["traceId"],
                        span_id=sp["spanId"],
                        parent_span_id=sp.get("parentSpanId") or None,
                        name=sp.get("name", ""),
                        kind=_KINDS.get(int(kind), "internal"),
                        start_time_ns=start,
                        end_time_ns=int(sp.get("endTimeUnixNano", start)) or start,
                        status=_STATUS.get(int(status), "unset"),
                        attributes=_json_attrs(sp.get("attributes")),
                        events=[
                            {
                                "name": e.get("name"),
                                "time_ns": int(e.get("timeUnixNano", 0)),
                                "attributes": _json_attrs(e.get("attributes")),
                            }
                            for e in sp.get("events", [])
                        ],
                        service_name=str(service) if service else None,
                    )
                )
    return spans


def parse_export(body: bytes, content_type: str) -> list[SpanRecord]:
    return parse_json(body) if "json" in content_type.lower() else parse_protobuf(body)


# --- enrichment ------------------------------------------------------------------------------


def operation_of(sp: SpanRecord) -> str | None:
    op = sp.attributes.get("gen_ai.operation.name")
    if op:
        return str(op)
    if sp.name == "retrieval":
        return "retrieval"
    return None


def resolve_agents(spans: list[SpanRecord], known: dict[str, str] | None = None) -> dict[str, str]:
    """span_id -> agent slug, via ``gen_ai.agent.name`` on the span or its nearest ancestor."""
    by_id = {s.span_id: s for s in spans}
    cache: dict[str, str | None] = {}

    def lookup(span_id: str, depth: int = 0) -> str | None:
        if span_id in cache:
            return cache[span_id]
        sp = by_id.get(span_id)
        if sp is None or depth > 64:
            return (known or {}).get(span_id)
        agent = sp.attributes.get("gen_ai.agent.name") or sp.attributes.get("pground.target")
        if agent:
            result: str | None = str(agent)
        elif sp.parent_span_id:
            result = lookup(sp.parent_span_id, depth + 1)
        else:
            result = None
        cache[span_id] = result
        return result

    return {s.span_id: a for s in spans if (a := lookup(s.span_id))}


def span_cost(sp: SpanRecord) -> tuple[int | None, int | None, float | None]:
    a = sp.attributes
    inp = a.get("gen_ai.usage.input_tokens")
    out = a.get("gen_ai.usage.output_tokens")
    cost = a.get("pground.cost_usd")
    if cost is None and inp is not None and out is not None:
        provider = str(a.get("gen_ai.provider.name") or a.get("gen_ai.system") or "")
        model = str(a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or "")
        ref = model if "/" in model else f"{provider}/{model}"
        cost = cost_usd(ref, Usage(input_tokens=int(inp), output_tokens=int(out)))
    return (
        int(inp) if inp is not None else None,
        int(out) if out is not None else None,
        float(cost) if cost is not None else None,
    )


def span_row(sp: SpanRecord, *, agent: str | None, source: str) -> dict[str, Any]:
    inp, out, cost = span_cost(sp)
    model = sp.attributes.get("gen_ai.response.model") or sp.attributes.get("gen_ai.request.model")
    return {
        "trace_id": sp.trace_id,
        "span_id": sp.span_id,
        "parent_span_id": sp.parent_span_id,
        "name": sp.name[:300],
        "kind": sp.kind,
        "start_time": datetime.fromtimestamp(sp.start_time_ns / 1e9, tz=UTC),
        "duration_ms": sp.duration_ms,
        "status": sp.status,
        "service": sp.service_name,
        "agent": agent,
        "operation": operation_of(sp),
        "model": str(model) if model else None,
        "input_tokens": inp,
        "output_tokens": out,
        "cost_usd": cost,
        "source": source,
        "attributes": sp.attributes,
        "events": sp.events,
    }
