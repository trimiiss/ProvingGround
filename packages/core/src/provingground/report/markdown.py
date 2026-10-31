"""Markdown reports for runs (comparison/gate markdown lives in compare.py / gate.py)."""

from __future__ import annotations

from ..metrics import format_metric
from ..models import Run
from .common import HEADLINE_METRICS, ci_text, failing_results, metric_label, projected_monthly_cost


def _reproducibility(run: Run) -> list[str]:
    lines = ["", "### Reproducibility", ""]
    if run.git:
        dirty = " (dirty)" if run.git.dirty else ""
        lines.append(f"- Commit: `{(run.git.sha or '?')[:10]}`{dirty} on `{run.git.ref or '?'}`")
    lines.append("- Datasets: " + ", ".join(f"`{s.name}`@`{s.dataset_version}`" for s in run.suites))
    lines.append("- Scorers: " + ", ".join(f"`{k}`@`{v}`" for k, v in sorted(run.scorer_versions.items())))
    if run.judge_model:
        lines.append(f"- Judge: `{run.judge_model}`")
    lines.append(f"- Settings: `{run.settings}`")
    return lines


def render_run_markdown(run: Run, *, requests_per_month: int = 1_000_000, max_failures: int = 15) -> str:
    s = run.summary
    out = [
        f"# Evaluation report — {run.target.name}",
        "",
        f"- **Target**: `{run.target.name}` ({run.target.kind}"
        + (f", model `{run.target.model}`" if run.target.model else "")
        + ")",
        f"- **Run**: `{run.id}`  •  {run.created_at:%Y-%m-%d %H:%M UTC}",
        f"- **Cases**: {s.n_cases}  •  **Results**: {s.n_results}",
    ]
    if s.pass_rate is not None:
        out.append(f"- **Overall pass rate**: {s.pass_rate * 100:.1f}%")
    statuses = ", ".join(f"{k}={v}" for k, v in sorted(s.status_counts.items()))
    out.append(f"- **Status**: {statuses}")
    if s.total_cost_usd is not None:
        monthly = projected_monthly_cost(run, requests_per_month)
        extra = f"  •  projected ${monthly:,.0f}/mo at {requests_per_month:,} req" if monthly else ""
        out.append(f"- **Cost**: ${s.total_cost_usd:.4f} this run (judge ${s.judge_cost_usd:.4f}){extra}")
    if s.cost_unknown_results:
        out.append(f"- ⚠️ cost unknown for {s.cost_unknown_results} result(s) (unpriced model)")

    out += ["", "## Metrics", "", "| Metric | Value | 95% CI | Pass rate |", "|---|---|---|---|"]
    shown = set()
    for metric, _ in HEADLINE_METRICS:
        stat = s.metrics.get(metric)
        if stat is None:
            continue
        shown.add(metric)
        out.append(f"| {metric_label(metric)} | {format_metric(metric, stat.mean)} | {ci_text(stat)} | "
                   f"{'—' if stat.pass_rate is None else f'{stat.pass_rate * 100:.0f}%'} |")  # fmt: skip
    for metric, stat in sorted(s.metrics.items()):
        if metric in shown or metric.startswith("perf.latency_p") or metric == "perf.latency_mean_ms":
            continue
        out.append(f"| {metric} | {format_metric(metric, stat.mean)} | {ci_text(stat)} | "
                   f"{'—' if stat.pass_rate is None else f'{stat.pass_rate * 100:.0f}%'} |")  # fmt: skip

    if s.by_suite:
        out += ["", "## By suite", "", "| Suite | Cases | Pass rate | Key metrics |", "|---|---|---|---|"]
        for suite in run.suites:
            rows = [r for r in run.results if r.suite == suite.name]
            verdicts = [r.passed for r in rows if r.passed is not None]
            pr = f"{sum(verdicts) / len(verdicts) * 100:.0f}%" if verdicts else "—"
            metrics = s.by_suite.get(suite.name, {})
            key = ", ".join(
                f"{metric_label(m)} {format_metric(m, metrics[m].mean)}"
                for m, _ in HEADLINE_METRICS
                if m in metrics and not m.startswith(("perf.", "cost.", "reliability."))
            )[:80]
            out.append(f"| `{suite.name}` | {suite.n_cases} | {pr} | {key} |")

    failures = failing_results(run, max_failures)
    if failures:
        out += ["", f"## Failing cases (showing {len(failures)})", ""]
        for r in failures:
            crit = " **[critical]**" if r.critical else ""
            out.append(f"### `{r.case_id}` ({r.suite}){crit}")
            out.append(f"- **Prompt**: {r.input_preview[:200]}")
            out.append(f"- **Answer**: {r.output.text[:200] or '(empty)'}")
            failed = [
                f"{sc.metric}={format_metric(sc.metric, sc.value)} ({sc.reason})"
                for sc in r.scores
                if sc.passed is False
            ]
            if failed:
                out.append(f"- **Failed**: {'; '.join(failed)[:300]}")
            out.append("")

    out += _reproducibility(run)
    return "\n".join(out) + "\n"
