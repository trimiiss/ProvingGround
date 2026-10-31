"""Prometheus metrics.

Two kinds:

* **Event metrics** (counters / histograms) are incremented as GenAI spans are ingested:
  requests, errors, latency, tokens, cost and tool calls per agent and model.
* **State metrics** (gauges) are read from PostgreSQL at scrape time: the latest evaluation
  scores per agent, the last gate verdict, attack success rate, online-eval scores and queue
  depth. Reading at scrape time means they are always current and survive restarts.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import text

from .db.session import session_scope

REGISTRY = CollectorRegistry(auto_describe=True)

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)

LLM_REQUESTS = Counter(
    "pground_llm_requests_total",
    "LLM calls observed in traces",
    ["agent", "model", "status"],
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "pground_llm_latency_seconds",
    "LLM call latency",
    ["agent", "model"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "pground_llm_tokens_total", "Tokens consumed", ["agent", "model", "direction"], registry=REGISTRY
)
LLM_COST = Counter(
    "pground_llm_cost_usd_total",
    "Spend in USD (estimated from the price table)",
    ["agent", "model"],
    registry=REGISTRY,
)
TOOL_CALLS = Counter(
    "pground_tool_calls_total", "Agent tool calls", ["agent", "tool", "status"], registry=REGISTRY
)
AGENT_REQUESTS = Counter(
    "pground_agent_requests_total", "Agent invocations", ["agent", "status"], registry=REGISTRY
)
AGENT_LATENCY = Histogram(
    "pground_agent_latency_seconds",
    "End-to-end agent latency",
    ["agent"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
SPANS_INGESTED = Counter(
    "pground_spans_ingested_total", "Spans received over OTLP", ["source"], registry=REGISTRY
)


def observe_span(row: dict[str, object]) -> None:
    """Update event metrics from one stored span row (see ``otlp.span_row``)."""
    agent = str(row.get("agent") or "unknown")
    model = str(row.get("model") or "unknown")
    status = "error" if row.get("status") == "error" else "ok"
    seconds = float(row["duration_ms"]) / 1000.0  # type: ignore[arg-type]
    op = row.get("operation")
    if row.get("source") != "live":
        return  # evaluation traffic is reported through eval gauges, not production traffic metrics
    if op == "chat":
        LLM_REQUESTS.labels(agent, model, status).inc()
        LLM_LATENCY.labels(agent, model).observe(seconds)
        if row.get("input_tokens"):
            LLM_TOKENS.labels(agent, model, "input").inc(float(row["input_tokens"]))  # type: ignore[arg-type]
        if row.get("output_tokens"):
            LLM_TOKENS.labels(agent, model, "output").inc(float(row["output_tokens"]))  # type: ignore[arg-type]
        if row.get("cost_usd"):
            LLM_COST.labels(agent, model).inc(float(row["cost_usd"]))  # type: ignore[arg-type]
    elif op == "execute_tool":
        tool = str((row.get("attributes") or {}).get("gen_ai.tool.name", "unknown"))  # type: ignore[attr-defined]
        TOOL_CALLS.labels(agent, tool, status).inc()
    elif op == "invoke_agent":
        AGENT_REQUESTS.labels(agent, status).inc()
        AGENT_LATENCY.labels(agent).observe(seconds)


EVAL_SCORE_SQL = text(
    """
    select distinct on (agent) agent, model, metrics, pass_rate, created_at
    from runs where kind = 'eval' order by agent, created_at desc
    """
)
GATE_SQL = text(
    "select distinct on (agent) agent, verdict, created_at from gate_evaluations order by agent, created_at desc"
)
REDTEAM_SQL = text(
    """
    select distinct on (agent) agent, metrics->>'safety.attack_success_rate' as asr, created_at
    from runs where kind = 'redteam' and metrics ? 'safety.attack_success_rate'
    order by agent, created_at desc
    """
)
ONLINE_SQL = text(
    """
    select agent, metric, avg(value) as v, count(*) as n from online_scores
    where created_at > now() - interval '1 hour' and value is not null group by agent, metric
    """
)
QUEUE_SQL = text("select status, count(*) from jobs group by status")

#: Evaluation metrics exported as gauges (others stay in the API / dashboard).
EXPORTED_EVAL_METRICS = (
    "quality.correctness",
    "factuality.faithfulness",
    "factuality.hallucination_rate",
    "safety.attack_success_rate",
    "safety.false_refusal_rate",
    "perf.latency_p95_ms",
    "cost.per_1k_requests_usd",
    "reliability.error_rate",
)


class Snapshot:
    """Rows fetched asynchronously, then rendered synchronously by the Prometheus client."""

    def __init__(self, eval_rows, gate_rows, asr_rows, online_rows, queue_rows) -> None:
        self.eval_rows, self.gate_rows, self.asr_rows = eval_rows, gate_rows, asr_rows
        self.online_rows, self.queue_rows = online_rows, queue_rows

    def families(self) -> list[GaugeMetricFamily]:
        score = GaugeMetricFamily(
            "pground_eval_score", "Latest evaluation metric per agent", labels=["agent", "model", "metric"]
        )
        for agent, model, metrics, _pass_rate, _created in self.eval_rows:
            for metric in EXPORTED_EVAL_METRICS:
                if metrics and metrics.get(metric) is not None:
                    score.add_metric([agent, model or "", metric], float(metrics[metric]))
        pass_rate = GaugeMetricFamily(
            "pground_eval_pass_rate", "Latest overall pass rate per agent", labels=["agent"]
        )
        for agent, _model, _metrics, rate, _created in self.eval_rows:
            if rate is not None:
                pass_rate.add_metric([agent], float(rate))
        gate = GaugeMetricFamily(
            "pground_gate_status", "Last gate verdict per agent (1 pass, 0.5 warn, 0 fail)", labels=["agent"]
        )
        for agent, verdict, _created in self.gate_rows:
            gate.add_metric([agent], {"PASS": 1.0, "WARN": 0.5}.get(verdict, 0.0))
        asr = GaugeMetricFamily(
            "pground_attack_success_rate", "Latest attack success rate per agent", labels=["agent"]
        )
        for agent, value, _created in self.asr_rows:
            if value is not None:
                asr.add_metric([agent], float(value))
        online = GaugeMetricFamily(
            "pground_online_score", "Mean online-eval score over the last hour", labels=["agent", "metric"]
        )
        for agent, metric, value, _n in self.online_rows:
            online.add_metric([agent, metric], float(value))
        queue = GaugeMetricFamily("pground_jobs", "Jobs by status", labels=["status"])
        seen = {status: n for status, n in self.queue_rows}
        for status in ("queued", "running", "succeeded", "failed"):
            queue.add_metric([status], float(seen.get(status, 0)))
        return [score, pass_rate, gate, asr, online, queue]


async def snapshot() -> Snapshot:
    async with session_scope() as session:
        return Snapshot(
            (await session.execute(EVAL_SCORE_SQL)).all(),
            (await session.execute(GATE_SQL)).all(),
            (await session.execute(REDTEAM_SQL)).all(),
            (await session.execute(ONLINE_SQL)).all(),
            (await session.execute(QUEUE_SQL)).all(),
        )


async def render() -> bytes:
    """Event metrics + a fresh state snapshot, in the Prometheus text format."""
    from prometheus_client import generate_latest

    out = generate_latest(REGISTRY)
    snap = await snapshot()
    lines = []
    for family in snap.families():
        lines.append(f"# HELP {family.name} {family.documentation}")
        lines.append(f"# TYPE {family.name} gauge")
        for sample in family.samples:
            labels = ",".join(f'{k}="{_escape(v)}"' for k, v in sample.labels.items())
            lines.append(
                f"{sample.name}{{{labels}}} {sample.value}" if labels else f"{sample.name} {sample.value}"
            )
    return out + ("\n".join(lines) + "\n").encode()


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
