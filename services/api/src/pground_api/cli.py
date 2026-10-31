"""`pground-api`: run the server, the worker, or database migrations."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help="ProvingGround platform server.")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "0.0.0.0",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the HTTP API (REST + OTLP ingest + /metrics)."""
    import uvicorn

    uvicorn.run("pground_api.main:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def worker() -> None:
    """Run a background worker (evaluation jobs and schedules)."""
    from .worker import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_worker())


@app.command()
def migrate(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Apply database migrations (alembic upgrade)."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ALEMBIC_INI)), revision)
    typer.echo(f"database at {revision}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
