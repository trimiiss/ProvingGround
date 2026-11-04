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
    label: Annotated[
        list[str] | None, typer.Option("--label", help="Attach key=value labels to the run")
    ] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML report when done")] = False,
) -> None:
    """Evaluate a target and save a run plus Markdown/HTML/JSON reports."""
    cfg = _load(config)
    settings = settings_from_config(cfg, sample=sample, repeats=repeats, concurrency=concurrency, seed=seed,
                                    use_cache=not no_cache)  # fmt: skip
    labels = {k: str(v) for k, v in _parse_overrides(label).items()}
    run_obj = _run_with_progress(cfg, target, _suite_list(suite), settings, _parse_overrides(set_), labels)
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
    labels: dict[str, str] | None = None,
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
                    cfg,
                    target,
                    suites=suites,
                    settings=settings,
                    overrides=overrides,
                    labels=labels,
                    progress=on_progress,
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
def compare(
    target: Annotated[str, typer.Option("--target", "-t")],
    baseline: Annotated[
        str, typer.Option("--baseline", "-b", help="file:<path> | run:<id> | latest | rerun:<ref>")
    ],
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
    sample: Annotated[int | None, typer.Option()] = None,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Candidate overrides, e.g. --set model=sim/model-b")
    ] = None,
    set_baseline: Annotated[
        list[str] | None, typer.Option("--set-baseline", help="Baseline overrides (A/B without git)")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the Markdown comparison here")
    ] = None,
) -> None:
    """Compare a candidate against a baseline, per case, with confidence intervals."""
    from .engine import compare_target
    from .report.compare_report import render_comparison_markdown

    cfg = _load(config)
    settings = settings_from_config(cfg, sample=sample)
    base_over = _parse_overrides(set_baseline) if set_baseline else None
    comparison, _, _ = asyncio.run(
        compare_target(
            cfg,
            target,
            baseline,
            suites=_suite_list(suite),
            settings=settings,
            candidate_overrides=_parse_overrides(set_),
            baseline_overrides=base_over,
        )
    )
    md = render_comparison_markdown(comparison, cfg)
    if output:
        output.write_text(md, encoding="utf-8")
    console.print(md, markup=False, highlight=False)


@app.command()
def gate(
    target: Annotated[str, typer.Option("--target", "-t")],
    policy: Annotated[str, typer.Option("--policy", "-p")] = "release",
    baseline: Annotated[
        str | None, typer.Option("--baseline", "-b", help="Override the policy's baseline")
    ] = None,
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
    sample: Annotated[int | None, typer.Option(help="Sample N cases per suite (keeps PR runs fast)")] = None,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Candidate overrides, e.g. --set model=sim/model-b")
    ] = None,
    set_baseline: Annotated[list[str] | None, typer.Option("--set-baseline")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="Write report.md / report.html / summary")
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Run the quality gate. Exit 0 = PASS/WARN, 1 = FAIL, 2 = pipeline error."""
    from .engine import gate_target
    from .gate import EXIT_ERROR
    from .report.compare_report import render_gate_html, render_gate_markdown

    cfg = _load(config)
    settings = settings_from_config(cfg, sample=sample)
    base_over = _parse_overrides(set_baseline) if set_baseline else None
    try:
        result, comparison, candidate, _ = asyncio.run(
            gate_target(
                cfg,
                target,
                policy,
                baseline_override=baseline,
                suites=_suite_list(suite),
                settings=settings,
                candidate_overrides=_parse_overrides(set_),
                baseline_overrides=base_over,
            )
        )
    except (ConfigError, KeyError) as exc:
        err.print(f"[red]gate error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from exc
    except Exception as exc:
        err.print(f"[red]gate pipeline error:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(EXIT_ERROR) from exc

    md = render_gate_markdown(result, comparison, candidate)
    if not quiet:
        console.print(md, markup=False, highlight=False)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.md").write_text(md, encoding="utf-8")
        (out_dir / "report.html").write_text(
            render_gate_html(result, comparison, candidate), encoding="utf-8"
        )
        (out_dir / "summary.json").write_text(result.model_dump_json(indent=1), encoding="utf-8")
    colour = {"PASS": "green", "WARN": "yellow", "FAIL": "red", "INCONCLUSIVE": "yellow"}[result.verdict]
    console.print(f"[bold {colour}]Gate {result.verdict}[/bold {colour}] (exit {result.exit_code})")
    raise typer.Exit(result.exit_code)


@app.command()
def leaderboard(
    target: Annotated[str, typer.Option("--target", "-t", help="Agent to sweep across models")],
    models: Annotated[
        str, typer.Option("--models", "-m", help="Comma-separated model refs")
    ] = "sim/model-a,sim/model-b,sim/model-c",
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
    sample: Annotated[int | None, typer.Option()] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Sweep a target across models and rank them, flagging the quality-vs-cost Pareto front."""
    from .compare import leaderboard as build_leaderboard

    cfg = _load(config)
    settings = settings_from_config(cfg, sample=sample)
    runs = []
    for model in [m.strip() for m in models.split(",") if m.strip()]:
        runs.append(asyncio.run(run_target(cfg, target, suites=_suite_list(suite), settings=settings,
                                           overrides={"model": model})))  # fmt: skip
    table = Table(
        "model", "pass rate", "correctness", "faithfulness", "attack success", "cost/1k", "p95", "pareto"
    )
    for row in build_leaderboard(runs):
        table.add_row(
            row.model or row.run_id,
            "—" if row.pass_rate is None else f"{row.pass_rate * 100:.0f}%",
            f"{row.metrics.get('quality.correctness', float('nan')):.3f}",
            f"{row.metrics.get('factuality.faithfulness', float('nan')):.3f}",
            f"{row.metrics.get('safety.attack_success_rate', float('nan')):.3f}",
            "—" if row.cost_per_1k is None else f"${row.cost_per_1k:.3f}",
            "—" if row.latency_p95_ms is None else f"{row.latency_p95_ms:.0f}ms",
            "★" if row.on_pareto_front else "",
        )
    console.print(table)


@app.command()
def doctor(
    target: Annotated[
        str | None, typer.Option("--target", "-t", help="Also measure detectors against this sim target")
    ] = None,
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
    ml: Annotated[
        bool, typer.Option("--ml", help="Also check the local PyTorch detectors (needs the [ml] extra)")
    ] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Validate graders (oracle/null/judge) and measure detector precision/recall."""
    from .doctor import run_doctor

    cfg = _load(config)
    report_obj = asyncio.run(run_doctor(cfg, target=target, suites=_suite_list(suite), ml=ml))
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


@app.command()
def redteam(
    target: Annotated[str, typer.Option("--target", "-t")],
    models: Annotated[
        str | None, typer.Option("--models", "-m", help="Comma-separated model refs to compare")
    ] = None,
    suite: Annotated[
        str | None, typer.Option("--suite", "-s", help="Default: the target's security-* suites")
    ] = None,
    sample: Annotated[int | None, typer.Option()] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Directory for redteam.md / redteam.html")
    ] = None,
) -> None:
    """Attack a target and report attack success rate by goal, technique and encoding."""
    from .redteam.report import render_redteam_html, render_redteam_markdown, summarize_attacks

    cfg = _load(config)
    target_cfg = cfg.target(target)
    suites = _suite_list(suite) or [
        s for s in target_cfg.suites if s.startswith("security-") or s == "over-refusal"
    ]
    if not suites:
        err.print(f"[red]{target} has no security-* suites; pass --suite[/red]")
        raise typer.Exit(2)
    settings = settings_from_config(cfg, sample=sample)
    store = RunStore(cfg.root)
    summaries = []
    for model in [m.strip() for m in models.split(",")] if models else [None]:
        overrides: dict[str, object] = {"model": model} if model else {}
        run_obj = _run_with_progress(cfg, target, suites, settings, overrides, {"kind": "redteam"})
        store.save(run_obj, render_run_reports(run_obj, requests_per_month=cfg.traffic.requests_per_month))
        summaries.append(summarize_attacks(run_obj))
    md = render_redteam_markdown(summaries)
    directory = output or store.dir_for(summaries[-1].run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "redteam.md").write_text(md, encoding="utf-8")
    (directory / "redteam.html").write_text(render_redteam_html(summaries), encoding="utf-8")
    console.print(md, markup=False, highlight=False)
    console.print(f"[green]wrote {directory / 'redteam.html'}[/green]")


@app.command()
def attack(
    target: Annotated[str, typer.Option("--target", "-t")],
    goal: Annotated[
        str | None,
        typer.Option("--goal", "-g", help="One goal; default: every goal that applies to the target"),
    ] = None,
    strategy: Annotated[
        str, typer.Option(help="search (offline) or llm (needs --attacker-model)")
    ] = "search",
    attacker_model: Annotated[
        str | None, typer.Option(help="Model ref for the llm strategy, e.g. anthropic/claude-opus-5-5")
    ] = None,
    budget: Annotated[int, typer.Option(help="Maximum queries per goal")] = 20,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Target overrides, e.g. --set model=sim/model-b")
    ] = None,
    seed: Annotated[int, typer.Option()] = 0,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Adaptive red teaming: keep attacking each goal until a detector fires or the budget is spent."""
    from .engine import build_one_target
    from .providers import ProviderRegistry, parse_model_ref
    from .redteam.adaptive import ADAPTIVE_GOALS, AdaptiveResult, LLMAttacker, SearchAttacker, adaptive_attack
    from .scorers.judge import build_judge

    cfg = _load(config)
    cfg.target(target)  # fail fast on an unknown target
    registry = ProviderRegistry(cfg.providers)
    if strategy == "llm" and not attacker_model:
        err.print("[red]--strategy llm needs --attacker-model[/red]")
        raise typer.Exit(2)

    async def go() -> list[AdaptiveResult]:
        tgt = build_one_target(cfg, target, _parse_overrides(set_))
        judge = build_judge(cfg.judge, registry)
        # Tool agents are attacked through their tools, chat agents through their words.
        tool_agent = bool(tgt.info.tool_policies)
        goals = (
            [goal]
            if goal
            else [
                g for g in ADAPTIVE_GOALS if (g in ("unauthorized_refund", "data_exfiltration")) == tool_agent
            ]
        )
        results = []
        try:
            for g in goals:
                if strategy == "llm":
                    provider, model = parse_model_ref(attacker_model or "")
                    strat: SearchAttacker | LLMAttacker = LLMAttacker(registry.get(provider), model)
                else:
                    strat = SearchAttacker(seed=seed)
                results.append(await adaptive_attack(tgt, judge, g, strat, budget=budget))
        finally:
            await tgt.aclose()
        return results

    try:
        results = asyncio.run(go())
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    table = Table(title=f"Adaptive attack on {target} ({strategy}, budget {budget})")
    for col in ("goal", "result", "queries", "winning technique", "evidence"):
        table.add_column(col)
    for r in results:
        win = r.attempts[-1] if r.succeeded else None
        table.add_row(
            r.goal,
            "[red]broken[/red]" if r.succeeded else "[green]held[/green]",
            str(r.queries_to_success or len(r.attempts)),
            f"{win.technique} / {win.converter}" if win else "—",
            ", ".join(win.evidence) if win else "—",
        )
    console.print(table)
    broken = sum(r.succeeded for r in results)
    console.print(
        f"{broken}/{len(results)} goals broken within {budget} queries each (model {results[0].model if results else '?'})"
    )


@app.command()
def push(
    run_ref: Annotated[str, typer.Argument(help="Run id, path, or 'latest'")] = "latest",
    label: Annotated[
        list[str] | None, typer.Option("--label", help="Add key=value labels, e.g. --label ref=main")
    ] = None,
    url: Annotated[str | None, typer.Option(envvar="PGROUND_API_URL")] = None,
    api_key: Annotated[str | None, typer.Option(envvar="PGROUND_API_KEY")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Upload a stored run to the platform (dashboard, history, and `api:` baselines)."""
    from .client import PlatformClient, PlatformError

    cfg = _load(config)
    run_obj = RunStore(cfg.root).load(run_ref)
    run_obj.labels = {**run_obj.labels, **{k: str(v) for k, v in _parse_overrides(label).items()}}
    client = PlatformClient(url, api_key)
    try:
        brief = client.push_run(run_obj)
    except PlatformError as exc:
        err.print(f"[red]push failed:[/red] {exc}")
        raise typer.Exit(2) from exc
    finally:
        client.close()
    console.print(
        f"[green]pushed[/green] {brief['id']} to {client.base_url}  (agent {brief['agent']}, {brief['n_results']} results)"
    )


@app.command("inspect")
def inspect_cmd(
    target: Annotated[str, typer.Option("--target", "-t")],
    task: Annotated[
        str, typer.Option("--task", help="Inspect task: path/to/file.py[@name] or a registry name")
    ],
    limit: Annotated[int | None, typer.Option(help="Evaluate at most N samples")] = None,
    set_: Annotated[list[str] | None, typer.Option("--set")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run an Inspect AI task (or an inspect_evals benchmark) against a target and store it as a run."""
    from .inspect_bridge import run_inspect_task

    cfg = _load(config)
    run_obj = asyncio.run(run_inspect_task(cfg, target, task, limit=limit, overrides=_parse_overrides(set_)))
    store = RunStore(cfg.root)
    directory = store.save(
        run_obj, render_run_reports(run_obj, requests_per_month=cfg.traffic.requests_per_month)
    )
    s = run_obj.summary
    console.print(f"[bold green]✓[/bold green] {run_obj.id}  pass_rate="
                  f"{'n/a' if s.pass_rate is None else f'{s.pass_rate * 100:.1f}%'}  samples={s.n_results}")  # fmt: skip
    console.print(f"  reports: {directory / 'report.md'}")


@app.command("gate-changed")
def gate_changed(
    base: Annotated[
        str, typer.Option("--base", help="Git ref to compare against, e.g. origin/main")
    ] = "origin/main",
    policy: Annotated[str, typer.Option("--policy", "-p")] = "release",
    all_targets: Annotated[
        bool, typer.Option("--all", help="Gate every target, not just changed ones")
    ] = False,
    sample: Annotated[int | None, typer.Option(help="Sample N cases per suite")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    out_dir: Annotated[Path, typer.Option("--out-dir")] = Path("gate-out"),
) -> None:
    """Gate every agent a PR changed (vs --base). Exit 0 PASS/WARN, 1 FAIL, 2 pipeline error."""
    from .affected import affected_targets
    from .engine import gate_target
    from .gate import EXIT_ERROR, EXIT_FAIL, EXIT_OK
    from .report.compare_report import render_gate_html, render_gate_markdown

    cfg = _load(config)
    try:
        affected = affected_targets(cfg, base)
    except Exception as exc:
        err.print(f"[red]cannot determine changed agents:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from exc
    names = sorted(cfg.all_targets()) if all_targets else affected.changed
    out_dir.mkdir(parents=True, exist_ok=True)
    console.print(
        f"changed: {affected.changed or '-'}  added: {affected.added or '-'}  removed: {affected.removed or '-'}"
    )
    if not names and not affected.added:
        (out_dir / "report.md").write_text(
            "## ✅ Quality gate: no agent changes\n\nNo AI agent configuration changed in this PR, so nothing to evaluate.\n",
            encoding="utf-8",
        )
        console.print("[green]No agent changes; gate skipped.[/green]")
        raise typer.Exit(EXIT_OK)

    settings = settings_from_config(cfg, sample=sample)
    code = EXIT_OK
    sections: list[str] = []
    summaries = []
    for name in [*names, *affected.added]:
        is_new = name in affected.added
        try:
            result, comparison, candidate, _ = asyncio.run(
                gate_target(
                    cfg,
                    name,
                    policy,
                    baseline_override="none" if is_new else f"rerun:{base}",
                    settings=settings,
                )
            )
        except Exception as exc:
            err.print(f"[red]{name}: pipeline error:[/red] {type(exc).__name__}: {exc}")
            code = EXIT_ERROR
            sections.append(f"## ❌ `{name}`: pipeline error\n\n`{type(exc).__name__}: {exc}`\n")
            continue
        target_dir = out_dir / name
        target_dir.mkdir(parents=True, exist_ok=True)
        md = render_gate_markdown(result, comparison, candidate)
        (target_dir / "report.md").write_text(md, encoding="utf-8")
        (target_dir / "report.html").write_text(
            render_gate_html(result, comparison, candidate), encoding="utf-8"
        )
        (target_dir / "summary.json").write_text(result.model_dump_json(indent=1), encoding="utf-8")
        sections.append(md)
        summaries.append({"target": name, "verdict": str(result.verdict), "exit_code": result.exit_code})
        console.print(f"  {name}: {result.verdict} (exit {result.exit_code})")
        if result.exit_code == EXIT_ERROR:
            code = EXIT_ERROR
        elif result.exit_code == EXIT_FAIL and code != EXIT_ERROR:
            code = EXIT_FAIL
    (out_dir / "report.md").write_text("\n---\n\n".join(sections), encoding="utf-8")
    import json

    (out_dir / "summary.json").write_text(
        json.dumps({"exit_code": code, "targets": summaries}, indent=1), encoding="utf-8"
    )
    verdict = {
        0: "[bold green]PASS[/bold green]",
        1: "[bold red]FAIL - merge blocked[/bold red]",
        2: "[bold yellow]ERROR[/bold yellow]",
    }[code]
    console.print(f"Overall: {verdict}")
    raise typer.Exit(code)


def load_dotenv_file() -> Path | None:
    """Load the nearest ``.env`` (this directory or a parent) into the environment, so provider keys
    such as ANTHROPIC_API_KEY work as the docs describe. Variables already set in the shell win."""
    from dotenv import find_dotenv, load_dotenv

    found = find_dotenv(usecwd=True)
    if not found:
        return None
    load_dotenv(found, override=False)
    return Path(found)


def main() -> None:
    load_dotenv_file()
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
