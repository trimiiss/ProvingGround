"""Markdown + HTML for comparisons and gate results (the PR comment and CI artifact)."""

from __future__ import annotations

from html import escape

from ..compare import Comparison, MetricDelta
from ..config import ProjectConfig
from ..gate import GateResult, Verdict
from ..metrics import format_metric
from ..models import Run
from .common import metric_label
from .html import html_shell

_VERDICT_EMOJI = {Verdict.PASS: "✅", Verdict.WARN: "⚠️", Verdict.FAIL: "❌", Verdict.INCONCLUSIVE: "❔"}
_VERDICT_WORD = {Verdict.PASS: "PASSED", Verdict.WARN: "PASSED WITH WARNINGS", Verdict.FAIL: "BLOCKED",
                 Verdict.INCONCLUSIVE: "INCONCLUSIVE"}  # fmt: skip


def _delta_str(d: MetricDelta | None) -> str:
    if d is None or d.delta is None:
        return "—"
    sign = "+" if d.delta >= 0 else "−"
    body = format_metric(d.metric, abs(d.delta)).lstrip("$")
    ci = ""
    if d.ci_low is not None and d.ci_high is not None:
        ci = f" (95% CI {format_metric(d.metric, d.ci_low)}…{format_metric(d.metric, d.ci_high)})"
    marker = "" if d.improved is None else (" ▲" if d.improved else " ▼")
    return f"{sign}{body}{marker}{ci}"


# --- markdown --------------------------------------------------------------------------------


def render_gate_markdown(result: GateResult, comparison: Comparison | None, candidate: Run) -> str:
    emoji = _VERDICT_EMOJI[result.verdict]
    out = [
        f"## {emoji} Quality gate `{result.policy}`: **{_VERDICT_WORD[result.verdict]}**",
        "",
        f"Target `{result.target}`"
        + (f" · model `{candidate.target.model}`" if candidate.target.model else "")
        + (f" · vs baseline `{result.baseline_id}`" if result.baseline_id else ""),
        "",
    ]
    if result.comparability_blocks:
        out += ["> **Runs are not comparable — gate could not decide:**", ""]
        out += [f"> - {b}" for b in result.comparability_blocks] + [""]

    out += ["| | Rule | Baseline | Candidate | Δ / threshold | Verdict |", "|---|---|---|---|---|---|"]
    for r in result.rules:
        d = comparison.delta(r.metric) if comparison and r.metric else None
        base = format_metric(r.metric or "", r.baseline) if r.baseline is not None else "—"
        cand = format_metric(r.metric or "", r.candidate) if r.candidate is not None else "—"
        change = _delta_str(d) if d else ""
        thresh = f"{change}<br>{r.threshold}".strip("<br>") if change else (r.detail or r.threshold)
        sev = "" if r.severity == "block" else " _(warn)_"
        out.append(
            f"| {_VERDICT_EMOJI[r.verdict]} | {r.rule}{sev} | {base} | {cand} | {thresh} | {r.verdict} |"
        )

    if comparison and comparison.regressions:
        out += ["", f"### ❌ Top regressions ({len(comparison.regressions)})", ""]
        for f in comparison.regressions[:8]:
            crit = " **[critical]**" if f.critical else ""
            out += [f"**`{f.case_id}`** ({f.suite}){crit}", f"- prompt: {f.preview[:160]}",
                    f"- baseline: {f.baseline_text[:160] or '(empty)'}",
                    f"- candidate: {f.candidate_text[:160] or '(empty)'}", ""]  # fmt: skip
    if comparison and comparison.fixes:
        out.append(f"### ✅ {len(comparison.fixes)} case(s) improved (fail → pass)")
    for note in result.notes:
        out.append(f"\n_{note}_")
    out += ["", "<sub>ProvingGround quality gate · "
            + (f"{comparison.n_common_cases} cases · " if comparison else "")
            + f"candidate `{result.candidate_id}`</sub>"]  # fmt: skip
    return "\n".join(out) + "\n"


# --- HTML ------------------------------------------------------------------------------------

_V_CLASS = {Verdict.PASS: "ok", Verdict.WARN: "wn", Verdict.FAIL: "err", Verdict.INCONCLUSIVE: "wn"}


def render_gate_html(result: GateResult, comparison: Comparison | None, candidate: Run) -> str:
    rows = ""
    for r in result.rules:
        d = comparison.delta(r.metric) if comparison and r.metric else None
        base = format_metric(r.metric or "", r.baseline) if r.baseline is not None else "—"
        cand = format_metric(r.metric or "", r.candidate) if r.candidate is not None else "—"
        rows += (
            f"<tr><td><span class='pill {_V_CLASS[r.verdict]}'>{r.verdict}</span></td>"
            f"<td>{escape(r.rule)}{'' if r.severity == 'block' else ' <span class=mut>(warn)</span>'}</td>"
            f"<td class=num>{base}</td><td class=num>{cand}</td>"
            f"<td>{escape(_delta_str(d))}</td><td class=mut>{escape(r.threshold)}</td></tr>"
        )
    reg = ""
    for f in comparison.regressions[:10] if comparison else []:
        reg += (
            f"<div class='case{' crit' if f.critical else ''}'><span class=id>{escape(f.case_id)} · {escape(f.suite)}</span>"
            f"<div class=txt><b>prompt:</b> {escape(f.preview[:200])}</div>"
            f"<div class=txt><b>baseline:</b> {escape(f.baseline_text[:200]) or '(empty)'}</div>"
            f"<div class=txt><b>candidate:</b> {escape(f.candidate_text[:200]) or '(empty)'}</div></div>"
        )
    blocks = "".join(f"<li>{escape(b)}</li>" for b in result.comparability_blocks)
    notes = "".join(f"<li>{escape(n)}</li>" for n in result.notes)
    model = f" · model <code>{escape(candidate.target.model)}</code>" if candidate.target.model else ""
    body = (
        f"<h1>{_VERDICT_EMOJI[result.verdict]} Gate <code>{escape(result.policy)}</code>: "
        f"<span class='pill {_V_CLASS[result.verdict]}'>{_VERDICT_WORD[result.verdict]}</span></h1>"
        f"<p class=sub>target <code>{escape(result.target)}</code>{model}"
        + (f" · baseline <code>{escape(result.baseline_id)}</code>" if result.baseline_id else "")
        + "</p>"
        + (f"<div class=case><b>Runs not comparable:</b><ul>{blocks}</ul></div>" if blocks else "")
        + "<h2>Rules</h2><table><tr><th>Verdict</th><th>Rule</th><th class=num>Baseline</th>"
        "<th class=num>Candidate</th><th>Δ</th><th>Threshold</th></tr>"
        f"{rows}</table>"
    )
    if reg:
        body += f"<h2>Top regressions</h2>{reg}"
    if notes:
        body += f"<div class=foot><ul>{notes}</ul></div>"
    return html_shell(f"Gate — {result.target}", body)


def render_comparison_markdown(comparison: Comparison, cfg: ProjectConfig | None = None) -> str:
    out = [
        f"# Comparison: {comparison.candidate_label} vs {comparison.baseline_label}",
        "",
        f"Target `{comparison.target}` · {comparison.n_common_cases} common cases · "
        f"candidate `{comparison.candidate_id}` vs baseline `{comparison.baseline_id}`",
        "",
        "| Metric | Baseline | Candidate | Δ (95% CI) | p |",
        "|---|---|---|---|---|",
    ]
    for metric, d in sorted(comparison.deltas.items()):
        p = f"{d.p_value:.3f}" if d.p_value is not None else ""
        out.append(f"| {metric_label(metric)} | {format_metric(metric, d.baseline)} | "
                   f"{format_metric(metric, d.candidate)} | {_delta_str(d)} | {p} |")  # fmt: skip
    if comparison.regressions:
        out += ["", f"## Regressions ({len(comparison.regressions)})", ""]
        for f in comparison.regressions[:15]:
            out.append(f"- `{f.case_id}` ({f.suite}){' **[critical]**' if f.critical else ''}")
    for issue in comparison.comparability:
        out.append(f"\n> {'❌' if issue.severity == 'block' else '⚠️'} {issue.message}")
    return "\n".join(out) + "\n"
