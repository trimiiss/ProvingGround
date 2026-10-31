"""Report rendering (markdown, HTML, JSON)."""

from __future__ import annotations

from ..models import Run
from .html import html_shell, render_run_html
from .markdown import render_run_markdown


def render_run_reports(run: Run, *, requests_per_month: int = 1_000_000) -> dict[str, str]:
    """The standard bundle of rendered reports saved alongside a run."""
    return {
        "report.md": render_run_markdown(run, requests_per_month=requests_per_month),
        "report.html": render_run_html(run, requests_per_month=requests_per_month),
        "run.json": run.model_dump_json(indent=1),
    }


__all__ = ["html_shell", "render_run_html", "render_run_markdown", "render_run_reports"]
