"""The ``pground`` command-line interface."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ConfigError, ProjectConfig, load_config
from .engine import run_target, settings_from_config
from .models import Run
from .report import render_run_reports
from .runner import RunSettings
from .store import RunStore


def _force_utf8() -> None:
    # Windows consoles default to cp1252, which can't encode the symbols we print.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


_force_utf8()

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="ProvingGround: evaluate, red-team and gate AI agents."
)
console = Console()
err = Console(stderr=True)


def _parse_overrides(values: list[str] | None) -> dict[str, object]:
    out: dict[str, object] = {}
    for item in values or []:
        if "=" not in item:
            raise typer.BadParameter(f"--set expects key=value, got {item!r}")
        key, raw = item.split("=", 1)
        out[key.strip()] = _coerce(raw.strip())
    return out


def _coerce(value: str) -> object:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def _load(config: Path | None) -> ProjectConfig:
    try:
        return load_config(config)
    except ConfigError as exc:
        err.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(2) from exc


def _suite_list(value: str | None) -> list[str] | None:
    return [s.strip() for s in value.split(",") if s.strip()] if value else None


@app.command()
def version() -> None:
    """Print the ProvingGround version."""
    console.print(f"ProvingGround {__version__}")


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Where to create provingground.yaml")] = Path("."),
) -> None:
    """Write a starter provingground.yaml."""
    path = directory / "provingground.yaml"
    if path.exists():
        err.print(f"[yellow]{path} already exists; not overwriting.[/yellow]")
        raise typer.Exit(1)
    path.write_text(_STARTER_CONFIG, encoding="utf-8")
    console.print(f"[green]Created {path}[/green]\nRun [bold]pground run --target <name>[/bold] to evaluate.")


@app.command("list")
def list_cmd(
    what: Annotated[str, typer.Argument(help="targets | suites | runs | gates")] = "targets",
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """List targets, suites, gate policies, or stored runs."""
    cfg = _load(config)
    if what == "runs":
        table = Table("run id", "target", "model", "results", "pass rate", "when")
        for e in RunStore(cfg.root).list():
            table.add_row(e.id, e.target, e.model or "", str(e.n_results),
                          "—" if e.pass_rate is None else f"{e.pass_rate * 100:.0f}%", f"{e.created_at:%Y-%m-%d %H:%M}")  # fmt: skip
        console.print(table)
        return
    if what == "targets":
        table = Table("target", "kind", "model", "suites", "description")
        for name, tc in sorted(cfg.all_targets().items()):
            table.add_row(name, tc.kind, tc.model or "", ",".join(tc.suites), tc.description[:50])
        console.print(table)
    elif what == "suites":
        table = Table("suite", "source", "scorers")
        for name, sc in sorted(cfg.suites.items()):
            source = sc.dataset or (sc.generator or {}).get("entrypoint", "generator")
            table.add_row(name, str(source), ",".join(s.name for s in sc.scorers))
        console.print(table)
    elif what == "gates":
        table = Table("gate", "baseline", "rules", "on_inconclusive")
        for name, g in sorted(cfg.gates.items()):
            table.add_row(name, g.baseline or "", str(len(g.rules)), g.on_inconclusive)
        console.print(table)
    else:
        raise typer.BadParameter("what must be one of: targets, suites, runs, gates")


@app.command()
def run(
    target: Annotated[str, typer.Option("--target", "-t", help="Target name from the config")],
    suite: Annotated[
        str | None, typer.Option("--suite", "-s", help="Comma-separated suites (default: the target's)")
    ] = None,
    sample: Annotated[
        int | None, typer.Option(help="Sample N cases per suite (stratified, deterministic)")
    ] = None,
    repeats: Annotated[int | None, typer.Option(help="Repeats per case")] = None,
    concurrency: Annotated[int | None, typer.Option("--concurrency", "-j")] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable the response cache")] = False,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Override target fields, e.g. --set model=sim/model-b")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML report when done")] = False,
) -> None:
    """Evaluate a target and save a run plus Markdown/HTML/JSON reports."""
    cfg = _load(config)
    settings = settings_from_config(cfg, sample=sample, repeats=repeats, concurrency=concurrency, seed=seed,
                                    use_cache=not no_cache)  # fmt: skip
    run_obj = _run_with_progress(cfg, target, _suite_list(suite), settings, _parse_overrides(set_))
    store = RunStore(cfg.root)
    directory = store.save(
        run_obj, render_run_reports(run_obj, requests_per_month=cfg.traffic.requests_per_month)
    )
    s = run_obj.summary
    console.print(f"\n[bold green]✓[/bold green] {run_obj.id}  pass_rate="
                  f"{'n/a' if s.pass_rate is None else f'{s.pass_rate * 100:.1f}%'}  "
                  f"results={s.n_results}  cost=${s.total_cost_usd or 0:.4f}")  # fmt: skip
    console.print(f"  reports: {directory / 'report.md'}")
    if open_report:
        typer.launch(str(directory / "report.html"))


def _run_with_progress(
    cfg: ProjectConfig,
    target: str,
    suites: list[str] | None,
    settings: RunSettings,
    overrides: dict[str, object],
) -> Run:
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=console) as progress:  # fmt: skip
        task = progress.add_task(f"evaluating {target}", total=None)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        try:
            return asyncio.run(
                run_target(
                    cfg, target, suites=suites, settings=settings, overrides=overrides, progress=on_progress
                )
            )
        except (ConfigError, KeyError) as exc:
            err.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(2) from exc


@app.command()
def report(
    run_ref: Annotated[str, typer.Argument(help="Run id, path, or 'latest'")] = "latest",
    fmt: Annotated[str, typer.Option("--format", "-f", help="md | html | json")] = "md",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Render a stored run as Markdown, HTML, or JSON."""
    cfg = _load(config)
    run_obj = RunStore(cfg.root).load(run_ref)
    reports = render_run_reports(run_obj, requests_per_month=cfg.traffic.requests_per_month)
    content = {"md": reports["report.md"], "html": reports["report.html"], "json": reports["run.json"]}[fmt]
    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"[green]wrote {output}[/green]")
    else:
        console.print(content) if fmt != "md" else console.print(content, markup=False, highlight=False)


@app.command()
def doctor(
    target: Annotated[
        str | None, typer.Option("--target", "-t", help="Also measure detectors against this sim target")
    ] = None,
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Validate graders (oracle/null/judge) and measure detector precision/recall."""
    from .doctor import run_doctor

    cfg = _load(config)
    report_obj = asyncio.run(run_doctor(cfg, target=target, suites=_suite_list(suite)))
    table = Table("check", "result", "detail")
    for c in report_obj.checks:
        table.add_row(c.name, "[green]PASS[/green]" if c.passed else "[red]FAIL[/red]", c.detail)
    console.print(table)
    for metric, stats in report_obj.detectors.items():
        console.print(f"detector [cyan]{metric}[/cyan]: precision={stats['precision']:.2f} recall={stats['recall']:.2f} "
                      f"(tp={stats['tp']} fp={stats['fp']} fn={stats['fn']})")  # fmt: skip
    if report_obj.ok:
        console.print("[bold green]✓ all grader checks passed[/bold green]")
    else:
        err.print("[bold red]✗ some grader checks failed[/bold red]")
        raise typer.Exit(1)


def main() -> None:
    app()


_STARTER_CONFIG = """\
project: my-project

judge:
  model: sim/judge        # swap for anthropic/claude-opus-5-5 (set ANTHROPIC_API_KEY) to use a real judge

defaults:
  concurrency: 8
  timeout_s: 60
  repeats: 1

targets:
  my-bot:
    kind: model
    model: sim/model-a
    system_prompt: "You are a helpful assistant."
    suites: [quality]

suites:
  quality:
    dataset: datasets/quality.jsonl
    scorers: [correctness, relevance]
"""


if __name__ == "__main__":
    main()
