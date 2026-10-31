"""Generate the Grafana dashboards (JSON) provisioned into the compose stack.

    python deploy/grafana/build_dashboards.py

The JSON in ``dashboards/`` is committed; this script is the readable source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


def target(
    expr: str, legend: str = "", *, instant: bool = False, ref: str = "A", fmt: str = "time_series"
) -> dict[str, Any]:
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend,
        "refId": ref,
        "instant": instant,
        "range": not instant,
        "format": fmt,
    }


class Board:
    def __init__(self, uid: str, title: str, *, templating: list[dict[str, Any]] | None = None) -> None:
        self.uid, self.title = uid, title
        self.panels: list[dict[str, Any]] = []
        self.templating = templating or []
        self._id = 0

    def _add(self, panel: dict[str, Any], x: int, y: int, w: int, h: int) -> None:
        self._id += 1
        self.panels.append(
            {"id": self._id, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h}, **panel}
        )

    def stat(
        self,
        title: str,
        expr: str,
        x: int,
        y: int,
        *,
        unit: str = "short",
        w: int = 4,
        h: int = 4,
        thresholds: list[tuple[float | None, str]] | None = None,
        decimals: int | None = None,
    ) -> None:
        steps = [{"color": c, "value": v} for v, c in (thresholds or [(None, "green")])]
        defaults: dict[str, Any] = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}
        if decimals is not None:
            defaults["decimals"] = decimals
        self._add({"type": "stat", "title": title, "targets": [target(expr, instant=True)],
                   "fieldConfig": {"defaults": defaults, "overrides": []},
                   "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"}}, x, y, w, h)  # fmt: skip

    def series(
        self,
        title: str,
        exprs: list[tuple[str, str]],
        x: int,
        y: int,
        *,
        unit: str = "short",
        w: int = 12,
        h: int = 8,
        stack: bool = False,
    ) -> None:
        custom: dict[str, Any] = {
            "lineWidth": 2,
            "fillOpacity": 12 if not stack else 40,
            "showPoints": "never",
        }
        if stack:
            custom["stacking"] = {"mode": "normal"}
        self._add({"type": "timeseries", "title": title,
                   "targets": [target(e, legend, ref=chr(65 + i)) for i, (e, legend) in enumerate(exprs)],
                   "fieldConfig": {"defaults": {"unit": unit, "custom": custom}, "overrides": []},
                   "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull"]}, "tooltip": {"mode": "multi"}}}, x, y, w, h)  # fmt: skip

    def bars(self, title: str, expr: str, legend: str, x: int, y: int, *, unit: str = "percentunit", w: int = 12, h: int = 9,
             vmin: float = 0.0, vmax: float | None = 1.0, bad_above: float | None = None, bad_below: float | None = None) -> None:  # fmt: skip
        if bad_below is not None:
            steps = [
                {"color": "red", "value": None},
                {"color": "yellow", "value": bad_below},
                {"color": "green", "value": min(1.0, bad_below + 0.15)},
            ]
        elif bad_above is not None:
            steps = [
                {"color": "green", "value": None},
                {"color": "yellow", "value": bad_above / 2},
                {"color": "red", "value": bad_above},
            ]
        else:
            steps = [{"color": "blue", "value": None}]
        defaults: dict[str, Any] = {
            "unit": unit,
            "min": vmin,
            "thresholds": {"mode": "absolute", "steps": steps},
        }
        if vmax is not None:
            defaults["max"] = vmax
        self._add({"type": "bargauge", "title": title, "targets": [target(expr, legend, instant=True)],
                   "fieldConfig": {"defaults": defaults, "overrides": []},
                   "options": {"displayMode": "gradient", "orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]},
                               "showUnfilled": True, "minVizHeight": 12}}, x, y, w, h)  # fmt: skip

    def row(self, title: str, y: int) -> None:
        self._id += 1
        self.panels.append(
            {
                "id": self._id,
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                "panels": [],
            }
        )

    def build(self) -> dict[str, Any]:
        return {
            "uid": self.uid, "title": self.title, "tags": ["provingground"], "timezone": "browser", "schemaVersion": 39, "version": 1,
            "editable": False, "refresh": "30s", "time": {"from": "now-6h", "to": "now"}, "panels": self.panels,
            "templating": {"list": self.templating}, "annotations": {"list": []},
        }  # fmt: skip

    def write(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{self.uid}.json").write_text(json.dumps(self.build(), indent=2) + "\n", encoding="utf-8")


def fleet() -> Board:
    b = Board("pground-fleet", "ProvingGround · Fleet overview")
    b.row("Right now", 0)
    b.stat(
        "Agents with a failing gate",
        "count(pground_gate_status == 0) or vector(0)",
        0,
        1,
        thresholds=[(None, "green"), (1, "red")],
    )
    b.stat("Requests / min", "sum(rate(pground_agent_requests_total[5m])) * 60", 4, 1, decimals=1)
    b.stat("Error rate", 'sum(rate(pground_agent_requests_total{status="error"}[5m])) / sum(rate(pground_agent_requests_total[5m]))', 8, 1,
           unit="percentunit", thresholds=[(None, "green"), (0.02, "yellow"), (0.05, "red")], decimals=2)  # fmt: skip
    b.stat("p95 latency", "histogram_quantile(0.95, sum by (le) (rate(pground_agent_latency_seconds_bucket[5m])))", 12, 1, unit="s",
           thresholds=[(None, "green"), (3, "yellow"), (5, "red")], decimals=2)  # fmt: skip
    b.stat(
        "Spend / hour",
        "sum(rate(pground_llm_cost_usd_total[15m])) * 3600",
        16,
        1,
        unit="currencyUSD",
        decimals=2,
    )
    b.stat("Worst attack success rate", "max(pground_attack_success_rate)", 20, 1, unit="percentunit",
           thresholds=[(None, "green"), (0.05, "yellow"), (0.10, "red")], decimals=1)  # fmt: skip
    b.row("Quality & safety (latest evaluation)", 5)
    b.bars(
        "Correctness by agent",
        'pground_eval_score{metric="quality.correctness"}',
        "{{agent}}",
        0,
        6,
        bad_below=0.75,
    )
    b.bars(
        "Attack success rate by agent (red-team)",
        "pground_attack_success_rate",
        "{{agent}}",
        12,
        6,
        bad_above=0.10,
    )
    b.bars(
        "Hallucination rate by agent",
        'pground_eval_score{metric="factuality.hallucination_rate"}',
        "{{agent}}",
        0,
        15,
        bad_above=0.12,
    )
    b.bars(
        "Gate status (1 pass · 0.5 warn · 0 fail)",
        "pground_gate_status",
        "{{agent}}",
        12,
        15,
        unit="short",
        bad_below=0.4,
    )
    b.row("Production traffic", 24)
    b.series(
        "Requests per minute",
        [("sum by (agent) (rate(pground_agent_requests_total[5m])) * 60", "{{agent}}")],
        0,
        25,
        stack=True,
    )
    b.series(
        "p95 latency by agent",
        [
            (
                "histogram_quantile(0.95, sum by (le, agent) (rate(pground_agent_latency_seconds_bucket[5m])))",
                "{{agent}}",
            )
        ],
        12,
        25,
        unit="s",
    )
    b.series(
        "Spend per hour by model",
        [("sum by (model) (rate(pground_llm_cost_usd_total[15m])) * 3600", "{{model}}")],
        0,
        33,
        unit="currencyUSD",
        stack=True,
    )
    b.series(
        "Token throughput",
        [("sum by (direction) (rate(pground_llm_tokens_total[5m]))", "{{direction}} tokens/s")],
        12,
        33,
    )
    b.series(
        "Tool calls per minute",
        [("sum by (tool, status) (rate(pground_tool_calls_total[5m])) * 60", "{{tool}} ({{status}})")],
        0,
        41,
    )
    b.series("Evaluation jobs", [("pground_jobs", "{{status}}")], 12, 41)
    return b


def agent() -> Board:
    var = {"name": "agent", "label": "Agent", "type": "query", "datasource": DS, "query": {"query": "label_values(pground_eval_score, agent)", "refId": "agents"},
           "refresh": 2, "sort": 1, "includeAll": False, "current": {"text": "support-bot", "value": "support-bot"}}  # fmt: skip
    b = Board("pground-agent", "ProvingGround · Agent detail", templating=[var])
    b.row("Latest evaluation", 0)
    b.stat(
        "Correctness",
        'pground_eval_score{agent="$agent",metric="quality.correctness"}',
        0,
        1,
        unit="percentunit",
        thresholds=[(None, "red"), (0.7, "yellow"), (0.85, "green")],
        decimals=1,
    )
    b.stat(
        "Faithfulness",
        'pground_eval_score{agent="$agent",metric="factuality.faithfulness"}',
        4,
        1,
        unit="percentunit",
        thresholds=[(None, "red"), (0.8, "yellow"), (0.9, "green")],
        decimals=1,
    )
    b.stat(
        "Attack success rate",
        'pground_attack_success_rate{agent="$agent"}',
        8,
        1,
        unit="percentunit",
        thresholds=[(None, "green"), (0.05, "yellow"), (0.10, "red")],
        decimals=1,
    )
    b.stat(
        "Eval p95 latency",
        'pground_eval_score{agent="$agent",metric="perf.latency_p95_ms"}',
        12,
        1,
        unit="ms",
        decimals=0,
    )
    b.stat(
        "Cost / 1k requests",
        'pground_eval_score{agent="$agent",metric="cost.per_1k_requests_usd"}',
        16,
        1,
        unit="currencyUSD",
        decimals=3,
    )
    b.stat(
        "Last gate",
        'pground_gate_status{agent="$agent"}',
        20,
        1,
        thresholds=[(None, "red"), (0.4, "yellow"), (0.9, "green")],
    )
    b.row("Production traffic", 5)
    b.series(
        "Requests per minute",
        [('sum by (status) (rate(pground_agent_requests_total{agent="$agent"}[5m])) * 60', "{{status}}")],
        0,
        6,
        stack=True,
    )
    b.series(
        "Latency",
        [
            (
                f'histogram_quantile({q}, sum by (le) (rate(pground_agent_latency_seconds_bucket{{agent="$agent"}}[5m])))',
                f"p{int(q * 100)}",
            )
            for q in (0.5, 0.95)
        ],
        12,
        6,
        unit="s",
    )
    b.series(
        "Spend per hour",
        [('sum by (model) (rate(pground_llm_cost_usd_total{agent="$agent"}[15m])) * 3600', "{{model}}")],
        0,
        14,
        unit="currencyUSD",
    )
    b.series(
        "Tokens per second",
        [('sum by (direction) (rate(pground_llm_tokens_total{agent="$agent"}[5m]))', "{{direction}}")],
        12,
        14,
    )
    b.series(
        "Tool calls per minute",
        [
            (
                'sum by (tool, status) (rate(pground_tool_calls_total{agent="$agent"}[5m])) * 60',
                "{{tool}} ({{status}})",
            )
        ],
        0,
        22,
    )
    b.series(
        "Online evaluation (last hour mean)",
        [('pground_online_score{agent="$agent"}', "{{metric}}")],
        12,
        22,
        unit="percentunit",
    )
    return b


if __name__ == "__main__":
    for board in (fleet(), agent()):
        board.write()
        print("wrote", OUT / f"{board.uid}.json", f"({len(board.panels)} panels)")
