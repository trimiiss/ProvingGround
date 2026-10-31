"""The demo agents as an HTTP service - what "another team's deployed agent" looks like.

    POST /agents/{slug}/chat   {input | messages, context?, case_id?}

The service continues the caller's W3C ``traceparent`` (so an evaluation's spans and the agent's
own spans form one trace) and exports its spans to the platform over OTLP when
``PGROUND_OTLP_ENDPOINT`` is set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from opentelemetry import context as otel_context
from opentelemetry import propagate
from pydantic import BaseModel

from provingground.models import Message
from provingground.tracing import setup_tracing

from .entry import DEFAULT_AGENTS_FILE, load_specs
from .runtime import Agent, AgentRequest


class ChatIn(BaseModel):
    input: str | None = None
    messages: list[Message] | None = None
    context: list[str] | None = None
    case_id: str | None = None
    sample: int = 0


class ChatOut(BaseModel):
    output: str
    status: str
    model: str | None
    tool_calls: list[dict[str, Any]]
    retrieved_context: list[str]
    usage: dict[str, int]
    cost_usd: float | None
    latency_ms: float | None
    trace_id: str | None
    error: str | None = None


def create_app(agents_file: str | None = None, root: str | None = None) -> FastAPI:
    specs = load_specs(
        agents_file or os.environ.get("DEMO_AGENTS_FILE", DEFAULT_AGENTS_FILE),
        root or os.environ.get("PGROUND_ROOT", "."),
    )
    agents = {slug: Agent(spec) for slug, spec in specs.items()}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        key = os.environ.get("PGROUND_INGEST_KEY")
        setup_tracing(
            service_name="acme-agents",
            otlp_endpoint=os.environ.get("PGROUND_OTLP_ENDPOINT"),
            otlp_headers={"x-api-key": key} if key else None,
        )
        yield

    app = FastAPI(title="Acme demo agents", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "agents": len(agents)}

    @app.get("/agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {"slug": s, "name": a.spec.name, "model": a.spec.model, "runtime": a.spec.runtime}
            for s, a in sorted(agents.items())
        ]

    @app.post("/agents/{slug}/chat", response_model=ChatOut)
    async def chat(slug: str, body: ChatIn, request: Request) -> ChatOut:
        agent = agents.get(slug)
        if agent is None:
            raise HTTPException(404, f"unknown agent {slug!r}")
        if body.input is None and not body.messages:
            raise HTTPException(422, "provide 'input' or 'messages'")
        token = otel_context.attach(propagate.extract(dict(request.headers)))  # continue the caller's trace
        try:
            out = await agent.run(
                AgentRequest(
                    input=body.input,
                    messages=body.messages,
                    context=body.context,
                    case_id=body.case_id,
                    sample=body.sample,
                )
            )
        except Exception as exc:
            raise HTTPException(503, f"{type(exc).__name__}: {exc}") from exc
        finally:
            otel_context.detach(token)
        return ChatOut(
            output=out.text, status=str(out.status), model=out.model_served, tool_calls=[tc.model_dump() for tc in out.tool_calls],
            retrieved_context=out.retrieved_context,
            usage={"input_tokens": out.usage.input_tokens, "output_tokens": out.usage.output_tokens,
                   "cache_read_tokens": out.usage.cache_read_tokens, "cache_write_tokens": out.usage.cache_write_tokens},
            cost_usd=out.cost_usd, latency_ms=out.latency_ms, trace_id=out.trace_id, error=out.error,
        )  # fmt: skip

    return app


app = create_app()
