"""Self-contained HTML reports (inline CSS + SVG, light/dark aware)."""

from __future__ import annotations

from html import escape
from typing import Any

from ..metrics import format_metric
from ..models import Run
from .common import HEADLINE_METRICS, failing_results, metric_label, pct, projected_monthly_cost

_STYLE = """
:root{color-scheme:light dark;--bg:#fbfbfd;--card:#fff;--fg:#1a1a2e;--muted:#6b7280;--line:#e5e7eb;
--good:#15803d;--goodbg:#dcfce7;--bad:#b91c1c;--badbg:#fee2e2;--warn:#b45309;--warnbg:#fef3c7;--accent:#4f46e5;--track:#ecedf2;}
@media(prefers-color-scheme:dark){:root{--bg:#0e0e14;--card:#17171f;--fg:#e8e8ef;--muted:#9aa0ac;--line:#262631;
--good:#4ade80;--goodbg:#14301f;--bad:#f87171;--badbg:#3a1414;--warn:#fbbf24;--warnbg:#3a2c0a;--accent:#818cf8;--track:#23232d;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:24px 16px}
.wrap{max-width:1000px;margin:0 auto}h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.2rem;margin:32px 0 12px;
border-bottom:1px solid var(--line);padding-bottom:6px}.sub{color:var(--muted);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card .k{color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:1.5rem;font-weight:650;margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:.9rem}
th{color:var(--muted);font-weight:600;font-size:.76rem;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:none}td.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:7px;border-radius:4px;background:var(--track);overflow:hidden;min-width:70px}
.bar>span{display:block;height:100%;background:var(--accent)}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:.78rem;font-weight:600}
.ok{color:var(--good);background:var(--goodbg)}.err{color:var(--bad);background:var(--badbg)}.wn{color:var(--warn);background:var(--warnbg)}
.case{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--bad);border-radius:10px;padding:12px 14px;margin:10px 0}
.case.crit{border-left-color:var(--bad);box-shadow:0 0 0 1px var(--bad) inset}
.case .id{font-family:ui-monospace,monospace;font-size:.82rem;color:var(--muted)}
.case .txt{margin:6px 0;white-space:pre-wrap;word-break:break-word}.mut{color:var(--muted)}
.foot{color:var(--muted);font-size:.8rem;margin-top:28px;border-top:1px solid var(--line);padding-top:12px}
code{background:var(--track);padding:1px 5px;border-radius:5px;font-size:.85em}
"""


def _bar(value: float, good: bool = True) -> str:
    color = "var(--good)" if good else "var(--bad)"
    return f'<div class="bar"><span style="width:{max(0, min(100, value * 100)):.0f}%;background:{color}"></span></div>'


def html_shell(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body><div class=wrap>{body}</div></body></html>"
    )


def _metric_rows(run: Run) -> str:
    rows = []
    shown = set()
    ordered = [m for m, _ in HEADLINE_METRICS if m in run.summary.metrics]
    ordered += [m for m in run.summary.metrics if m not in shown and m not in ordered
                and not (m.startswith("perf.latency_p") and m != "perf.latency_p95_ms")]  # fmt: skip
    for metric in ordered:
        stat = run.summary.metrics.get(metric)
        if stat is None or metric in shown:
            continue
        shown.add(metric)
        is_score = stat.direction == "higher" and stat.mean is not None and stat.mean <= 1.0
        bar = _bar(stat.mean or 0, good=True) if is_score else ""
        ci = (f'<span class=mut>[{format_metric(metric, stat.ci_low)}, {format_metric(metric, stat.ci_high)}]</span>'
              if stat.ci_low is not None else "")  # fmt: skip
        rows.append(f"<tr><td>{escape(metric_label(metric))}<br><span class=mut style='font-size:.75rem'>{escape(metric)}</span></td>"
                    f"<td class=num>{format_metric(metric, stat.mean)}</td><td>{bar}</td><td>{ci}</td>"
                    f"<td class=num>{'—' if stat.pass_rate is None else f'{stat.pass_rate * 100:.0f}%'}</td></tr>")  # fmt: skip
    return "".join(rows)


def render_run_html(run: Run, *, requests_per_month: int = 1_000_000, max_failures: int = 25) -> str:
    s = run.summary
    cards: list[tuple[str, str, str]] = [("Pass rate", pct(s.pass_rate), "")]
    for metric in ("quality.correctness", "factuality.hallucination_rate", "safety.attack_success_rate",
                   "perf.latency_p95_ms", "cost.per_1k_requests_usd"):  # fmt: skip
        stat = s.metrics.get(metric)
        if stat:
            cards.append((metric_label(metric), format_metric(metric, stat.mean), ""))
    monthly = projected_monthly_cost(run, requests_per_month)
    if monthly:
        cards.append(("Projected monthly", f"${monthly:,.0f}", f"at {requests_per_month:,} req"))
    card_html = "".join(
        f"<div class=card><div class=k>{escape(k)}</div><div class=v>{escape(v)}</div>"
        f"{f'<div class=mut style=font-size:.75rem>{escape(note)}</div>' if note else ''}</div>"
        for k, v, note in cards
    )

    status_html = " ".join(
        f'<span class="pill {"err" if k in ("error", "timeout", "model_mismatch") else "ok" if k == "ok" else "wn"}">{k}: {v}</span>'
        for k, v in sorted(s.status_counts.items())
    )

    suite_rows = []
    for suite in run.suites:
        rows = [r for r in run.results if r.suite == suite.name]
        verdicts = [r.passed for r in rows if r.passed is not None]
        pr = sum(verdicts) / len(verdicts) if verdicts else None
        suite_rows.append(f"<tr><td><code>{escape(suite.name)}</code></td><td class=num>{suite.n_cases}</td>"
                          f"<td class=num>{pct(pr)}</td><td>{_bar(pr or 0) if pr is not None else ''}</td></tr>")  # fmt: skip

    failures = failing_results(run, max_failures)
    fail_html = ""
    for r in failures:
        failed = "; ".join(
            f"<code>{escape(sc.metric)}</code> {escape(sc.reason)}" for sc in r.scores if sc.passed is False
        )
        fail_html += (
            f"<div class='case{' crit' if r.critical else ''}'><span class=id>{escape(r.case_id)} · {escape(r.suite)}"
            f"{' · CRITICAL' if r.critical else ''}</span>"
            f"<div class=txt><b>Q:</b> {escape(r.input_preview[:260])}</div>"
            f"<div class=txt><b>A:</b> {escape(r.output.text[:260]) or '<span class=mut>(empty)</span>'}</div>"
            f"<div class=mut style='font-size:.82rem'>{failed}</div></div>"
        )

    model = f", model <code>{escape(run.target.model)}</code>" if run.target.model else ""
    body = (
        f"<h1>Evaluation report</h1>"
        f"<p class=sub><code>{escape(run.target.name)}</code> ({escape(run.target.kind)}{model}) · "
        f"run <code>{escape(run.id)}</code> · {run.created_at:%Y-%m-%d %H:%M UTC}</p>"
        f"<div class=cards>{card_html}</div><p style='margin-top:14px'>{status_html}</p>"
        f"<h2>Metrics</h2><table><tr><th>Metric</th><th class=num>Value</th><th></th><th>95% CI</th><th class=num>Pass</th></tr>{_metric_rows(run)}</table>"
        f"<h2>By suite</h2><table><tr><th>Suite</th><th class=num>Cases</th><th class=num>Pass rate</th><th></th></tr>{''.join(suite_rows)}</table>"
    )
    if fail_html:
        body += f"<h2>Failing cases ({len(failures)})</h2>{fail_html}"
    body += (
        "<div class=foot>"
        + " · ".join(
            filter(
                None,
                [
                    f"commit {escape((run.git.sha or '?')[:10])}" if run.git else "",
                    "datasets "
                    + ", ".join(f"{escape(s.name)}@{escape(s.dataset_version)}" for s in run.suites),
                    f"judge {escape(run.judge_model)}" if run.judge_model else "",
                ],
            )
        )
        + "</div>"
    )
    return html_shell(f"Eval — {run.target.name}", body)


def render_run(run: Run, extra: dict[str, Any] | None = None) -> str:
    opts = extra or {}
    return render_run_html(run, requests_per_month=opts.get("requests_per_month", 1_000_000))
