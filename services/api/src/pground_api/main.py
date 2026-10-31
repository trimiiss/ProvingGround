"""FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import metrics
from .db.session import dispose_engine, init_engine, session_scope
from .routers import compare, data, fleet, runs, traces
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_engine()
    try:
        async with session_scope() as session:
            await data.sync_config(session)
    except Exception:
        import logging

        logging.getLogger("pground_api").exception("initial config sync failed")
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ProvingGround API",
        version="0.1.0",
        description="Evaluation, red-teaming and reliability platform for AI agents.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    for router in (
        traces.ingest_router,
        traces.router,
        runs.router,
        fleet.router,
        compare.router,
        data.router,
    ):
        app.include_router(router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        async with session_scope() as session:
            await session.execute(text("select 1"))
        return {"status": "ok"}

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(await metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()
