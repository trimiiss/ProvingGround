from __future__ import annotations

import gzip
import json

import httpx
import pytest

from provingground.config import load_config
from provingground.engine import run_target, settings_from_config
from provingground.models import Run

from .conftest import ADMIN, INGEST, ROOT

SUITES = ["customer-quality", "customer-factuality", "security-rag"]


async def make_run(
    agent: str = "support-bot", model: str = "sim/model-a", sample: int = 30, suites: list[str] | None = None
) -> Run:
    cfg = load_config(ROOT / "provingground.yaml")
    settings = settings_from_config(cfg, sample=sample, seed=3, use_cache=False, n_boot=100)
    return await run_target(
        cfg, agent, suites=suites or SUITES, settings=settings, overrides={"model": model}
    )


async def push(client: httpx.AsyncClient, run: Run) -> None:
    resp = await client.post(
        "/v1/runs", content=run.model_dump_json(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 201, resp.text


# --- auth & fleet ----------------------------------------------------------------------------


async def test_auth_is_enforced(anon: httpx.AsyncClient) -> None:
    assert (await anon.get("/healthz")).status_code == 200  # open
    assert (await anon.get("/v1/agents")).status_code == 401
    assert (await anon.get("/v1/agents", headers={"x-api-key": "wrong"})).status_code == 401
    assert (await anon.get("/v1/agents", headers=ADMIN)).status_code == 200
    # The ingest key may write traces but not read anything.
    assert (await anon.get("/v1/agents", headers=INGEST)).status_code == 401
    assert (
        await anon.post(
            "/v1/traces", content=b"", headers={**INGEST, "content-type": "application/x-protobuf"}
        )
    ).status_code == 200
    assert (
        await anon.post("/v1/traces", content=b"", headers={"content-type": "application/x-protobuf"})
    ).status_code == 401


async def test_sync_creates_the_fleet(client: httpx.AsyncClient) -> None:
    counts = (await client.post("/v1/admin/sync")).json()
    assert counts["agents"] == 20 and counts["policies"] >= 1 and counts["datasets"] >= 10
    agents = (await client.get("/v1/agents")).json()
    assert len(agents) == 20 and "support-bot-http" not in {
        a["slug"] for a in agents
    }  # fleet: false is excluded
    assert (await client.post("/v1/admin/sync")).json()["datasets"] == 0  # idempotent
    names = {d["name"] for d in (await client.get("/v1/datasets")).json()}
    assert {"customer-quality", "security-rag"} <= names


# --- runs ------------------------------------------------------------------------------------


async def test_run_round_trip_and_filters(client: httpx.AsyncClient) -> None:
    run = await make_run(model="sim/model-b")
    await push(client, run)
    listed = (await client.get("/v1/runs", params={"agent": "support-bot"})).json()
    assert (
        listed["total"] == 1
        and listed["items"][0]["id"] == run.id
        and listed["items"][0]["model"] == "sim/model-b"
    )

    detail = (await client.get(f"/v1/runs/{run.id}")).json()
    assert detail["n_results"] == run.summary.n_results and "quality.correctness" in detail["metrics"]
    assert detail["summary"]["status_counts"]["ok"] > 0

    failing = (await client.get(f"/v1/runs/{run.id}/results", params={"passed": "false", "limit": 5})).json()
    assert failing["total"] > 0 and all(r["passed"] is False for r in failing["items"])
    by_suite = (await client.get(f"/v1/runs/{run.id}/results", params={"suite": "security-rag"})).json()
    assert by_suite["total"] == 30 and {r["suite"] for r in by_suite["items"]} == {"security-rag"}
    metric = (
        await client.get(f"/v1/runs/{run.id}/results", params={"metric_failed": "safety.attack_success_rate"})
    ).json()
    assert metric["total"] > 0  # the budget model loses attacks
    search = (await client.get(f"/v1/runs/{run.id}/results", params={"q": "return"})).json()
    assert search["total"] > 0

    one = (await client.get(f"/v1/results/{failing['items'][0]['id']}")).json()
    assert one["payload"]["scores"] and one["payload"]["case_id"] == failing["items"][0]["case_id"]

    rt = (await client.get(f"/v1/runs/{run.id}/redteam")).json()
    assert rt["attempts"] > 0 and rt["asr"] > 0.2
    for fmt, marker in (("md", "# Evaluation report"), ("html", "<!doctype html"), ("json", '"results"')):
        body = (await client.get(f"/v1/runs/{run.id}/report", params={"format": fmt})).text
        assert marker in body

    assert (await client.delete(f"/v1/runs/{run.id}")).status_code == 204
    assert (await client.get(f"/v1/runs/{run.id}")).status_code == 404


async def test_import_is_idempotent_and_survives_reload(client: httpx.AsyncClient) -> None:
    run = await make_run(sample=10)
    await push(client, run)
    await push(client, run)  # replace, not duplicate
    assert (await client.get("/v1/runs")).json()["total"] == 1
    from pground_api import store
    from pground_api.db.session import session_scope

    async with session_scope() as session:
        loaded = await store.load_run(session, run.id)
    assert loaded is not None and len(loaded.results) == len(run.results)
    assert loaded.summary.pass_rate == run.summary.pass_rate


# --- compare & gate --------------------------------------------------------------------------


async def test_compare_and_gate_through_the_api(client: httpx.AsyncClient) -> None:
    await client.post("/v1/admin/sync")
    base = await make_run(model="sim/model-a", sample=60)
    bad = await make_run(model="sim/model-b", sample=60)
    good = await make_run(model="sim/model-c", sample=60)
    for r in (base, bad, good):
        await push(client, r)

    cmp_ = (await client.get("/v1/compare", params={"baseline": base.id, "candidate": bad.id})).json()
    assert cmp_["n_common_cases"] > 100
    assert cmp_["deltas"]["safety.attack_success_rate"]["delta"] > 0.2
    assert any(f["kind"] == "regression" for f in cmp_["flips"])

    verdict_bad = (
        await client.post(
            "/v1/gates/evaluate",
            json={"policy": "release", "candidate_run_id": bad.id, "baseline_run_id": base.id},
        )
    ).json()
    assert verdict_bad["verdict"] == "FAIL" and verdict_bad["exit_code"] == 1
    verdict_good = (
        await client.post(
            "/v1/gates/evaluate",
            json={"policy": "release", "candidate_run_id": good.id, "baseline_run_id": base.id},
        )
    ).json()
    assert verdict_good["verdict"] in ("PASS", "WARN")

    history = (await client.get("/v1/gates/evaluations", params={"agent": "support-bot"})).json()
    assert [h["verdict"] for h in history][:2] == [verdict_good["verdict"], "FAIL"]  # newest first
    overview = (await client.get("/v1/overview")).json()
    agent = next(a for a in overview["agents"] if a["agent"]["slug"] == "support-bot")
    assert agent["gate"]["verdict"] == verdict_good["verdict"] and agent["latest_run"] is not None
    assert (await client.get("/v1/gates/evaluate-missing")).status_code == 404

    # a dry run returns the verdict without adding to the gate history
    dry = await client.post(
        "/v1/gates/evaluate",
        params={"record": "false"},
        json={"policy": "release", "candidate_run_id": bad.id, "baseline_run_id": base.id},
    )
    assert dry.json()["verdict"] == "FAIL"
    assert len((await client.get("/v1/gates/evaluations", params={"agent": "support-bot"})).json()) == len(
        history
    )


# --- jobs ------------------------------------------------------------------------------------


async def test_job_lifecycle_through_the_worker(client: httpx.AsyncClient) -> None:
    from pground_api import queue, worker

    job = (
        await client.post(
            "/v1/jobs", json={"kind": "run", "payload": {"target": "ticket-classifier", "sample": 8}}
        )
    ).json()
    assert job["status"] == "queued"
    claimed = await queue.claim("test-worker")
    assert claimed is not None and str(claimed.id) == job["id"]
    await worker.execute(claimed, "test-worker")
    done = (await client.get(f"/v1/jobs/{job['id']}")).json()
    assert done["status"] == "succeeded" and done["result"]["n_results"] > 0 and done["progress"] == 1.0
    assert (await client.get(f"/v1/runs/{done['result']['run_id']}")).status_code == 200


async def test_fleet_wide_run_fans_out_one_job_per_agent(client: httpx.AsyncClient) -> None:
    from pground_api import queue, worker

    job = (
        await client.post("/v1/jobs", json={"kind": "run", "payload": {"target": "*", "sample": 2}})
    ).json()
    claimed = await queue.claim("test-worker")
    assert claimed is not None and str(claimed.id) == job["id"]
    await worker.execute(claimed, "test-worker")
    result = (await client.get(f"/v1/jobs/{job['id']}")).json()["result"]
    assert result["enqueued"] == 20 and "support-bot" in result["targets"]
    queued = (await client.get("/v1/jobs", params={"status": "queued", "limit": 50})).json()
    assert queued["total"] == 20 and {j["payload"]["target"] for j in queued["items"]} == set(
        result["targets"]
    )


async def test_online_eval_scores_sampled_live_traffic_once(client: httpx.AsyncClient) -> None:
    from pground_api import queue, worker

    await client.post("/v1/admin/sync")
    # Prometheus counters are process-wide, so this uses an agent no other test sends traffic for.
    for error in (False, False, True):
        body, ctype = otlp_payload("returns-assistant", error=error)
        await client.post("/v1/traces", content=body, headers={**INGEST, "content-type": ctype})

    async def run_job() -> dict:
        job = (await client.post("/v1/jobs", json={"kind": "online_eval", "payload": {"rate": 1.0}})).json()
        claimed = await queue.claim("test-worker")
        assert claimed is not None and str(claimed.id) == job["id"]
        await worker.execute(claimed, "test-worker")
        return (await client.get(f"/v1/jobs/{job['id']}")).json()["result"]

    first = await run_job()
    assert first["scored"] == 2  # the errored request is left to the error-rate metrics
    assert (await run_job())["scored"] == 0  # every trace is scored once
    agent = (await client.get("/v1/agents/returns-assistant")).json()
    assert agent["online"]["factuality.faithfulness"] == 1.0  # "30 days." is supported by the docs
    assert agent["online"]["safety.canary_leak_rate"] == 0.0 and "quality.refusal_rate" in agent["online"]
    metrics = (await client.get("/metrics")).text
    assert 'pground_online_score{agent="returns-assistant",metric="factuality.faithfulness"} 1.0' in metrics


async def test_unknown_target_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/jobs", json={"kind": "run", "payload": {"target": "nope"}})
    assert resp.status_code == 422 and "unknown target" in resp.text


async def test_gate_job_blocks_a_regression(client: httpx.AsyncClient) -> None:
    from pground_api import queue, worker

    await client.post("/v1/admin/sync")
    payload = {"target": "support-bot", "policy": "release", "candidate_overrides": {"model": "sim/model-b"},
               "baseline_overrides": {"model": "sim/model-a"}, "suites": ["customer-quality", "security-rag"]}  # fmt: skip
    job = (await client.post("/v1/jobs", json={"kind": "gate", "payload": payload})).json()
    claimed = await queue.claim("w")
    assert claimed is not None
    await worker.execute(claimed, "w")
    result = (await client.get(f"/v1/jobs/{job['id']}")).json()["result"]
    assert result["verdict"] == "FAIL" and result["exit_code"] == 1
    evaluation = (await client.get(f"/v1/gates/evaluations/{result['evaluation_id']}")).json()
    assert evaluation["agent"] == "support-bot"


async def test_queue_claims_are_exclusive_and_ordered(db: None) -> None:
    import asyncio

    from pground_api import queue
    from pground_api.db.session import session_scope

    async with session_scope() as session:
        for i in range(6):
            await queue.enqueue(session, "run", {"n": i}, priority=1 if i == 5 else 0)
    claims = await asyncio.gather(*(queue.claim(f"w{i}") for i in range(8)))
    got = [c for c in claims if c is not None]
    assert len(got) == 6 and len({c.id for c in got}) == 6  # SKIP LOCKED: no job is handed out twice
    assert len([c for c in claims if c is None]) == 2
    assert await queue.claim("late") is None


async def test_failed_job_retries_then_fails_and_stale_jobs_are_reaped(db: None) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from pground_api import queue
    from pground_api.db import models as m
    from pground_api.db.session import session_scope

    async with session_scope() as session:
        job = await queue.enqueue(session, "run", {}, max_attempts=2)
    claimed = await queue.claim("w")
    assert claimed is not None
    assert await queue.fail(claimed.id, "boom") == "queued"  # attempts remain -> requeued with backoff
    async with session_scope() as session:
        await session.execute(update(m.Job).where(m.Job.id == job.id).values(run_after=datetime.now(UTC)))
    again = await queue.claim("w")
    assert again is not None and again.attempts == 2
    assert await queue.fail(again.id, "boom again") == "failed"  # exhausted

    async with session_scope() as session:
        stale = await queue.enqueue(session, "run", {})
    c2 = await queue.claim("dead-worker")
    assert c2 is not None
    async with session_scope() as session:
        await session.execute(
            update(m.Job)
            .where(m.Job.id == stale.id)
            .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=10))
        )
    assert await queue.reap_stale(60) == 1
    assert (await queue.claim("w2")) is not None  # back in the queue for another worker
    assert await queue.heartbeat(stale.id, "dead-worker") is False  # the old worker lost its lease


async def test_schedules_fire_jobs(client: httpx.AsyncClient) -> None:
    from sqlalchemy import update

    from pground_api import worker
    from pground_api.db import models as m
    from pground_api.db.session import session_scope

    resp = await client.put(
        "/v1/schedules/nightly",
        json={
            "name": "nightly",
            "cron": "0 3 * * *",
            "job_kind": "run",
            "payload": {"target": "support-bot"},
        },
    )
    assert resp.status_code == 200 and resp.json()["next_run_at"]
    assert (
        await client.put("/v1/schedules/bad", json={"name": "bad", "cron": "not a cron", "job_kind": "run"})
    ).status_code == 422
    assert await worker.fire_due_schedules() == 0  # not due yet
    async with session_scope() as session:
        from datetime import UTC, datetime, timedelta

        await session.execute(update(m.Schedule).values(next_run_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert await worker.fire_due_schedules() == 1
    jobs = (await client.get("/v1/jobs", params={"status": "queued"})).json()
    assert jobs["total"] == 1 and jobs["items"][0]["payload"]["target"] == "support-bot"


# --- traces ----------------------------------------------------------------------------------


def otlp_payload(agent: str = "support-bot", *, error: bool = False) -> tuple[bytes, str]:
    """Spans made by the real OpenTelemetry SDK and serialised by the real OTLP encoder."""
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from provingground.models import Usage
    from provingground.tracing import agent_span, llm_span, record_io, record_llm_result

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "acme-agents"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    import provingground.tracing as t

    tracer = provider.get_tracer("test")
    original = t.get_tracer
    t.get_tracer = lambda name="provingground": tracer  # type: ignore[assignment]
    try:
        with agent_span(agent) as span:
            record_io(
                span,
                input_text="How long do I have to return boots?",
                output_text="30 days.",
                docs=["Footwear: 30 days."],
            )
            with llm_span("sim", "model-a") as llm:
                record_llm_result(llm, response_model="model-a", usage=Usage(input_tokens=100, output_tokens=20), cost_usd=0.0012,
                                  stop_reason="end", latency_ms=420.0)  # fmt: skip
            if error:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, "upstream 503"))
    finally:
        t.get_tracer = original  # type: ignore[assignment]
    return encode_spans(exporter.get_finished_spans()).SerializeToString(), "application/x-protobuf"


async def test_otlp_protobuf_ingest_enriches_and_exposes_spans(client: httpx.AsyncClient) -> None:
    body, ctype = otlp_payload()
    resp = await client.post("/v1/traces", content=body, headers={**INGEST, "content-type": ctype})
    assert resp.status_code == 200
    again = await client.post("/v1/traces", content=body, headers={**INGEST, "content-type": ctype})
    assert again.status_code == 200  # idempotent: duplicate spans are ignored

    traces = (await client.get("/v1/traces", params={"hours": 1})).json()
    assert len(traces) == 1 and traces[0]["agent"] == "support-bot" and traces[0]["n_spans"] == 2
    assert (
        traces[0]["input"].startswith("How long")
        and traces[0]["input_tokens"] == 100
        and traces[0]["cost_usd"] == pytest.approx(0.0012)
    )

    detail = (await client.get(f"/v1/traces/{traces[0]['trace_id']}")).json()
    chat = next(s for s in detail["spans"] if s["operation"] == "chat")
    assert (
        chat["agent"] == "support-bot" and chat["model"] == "model-a" and chat["parent_span_id"]
    )  # agent inherited from the parent

    text = (await client.get("/metrics")).text
    assert 'pground_llm_requests_total{agent="support-bot",model="model-a",status="ok"} 1.0' in text
    assert 'pground_llm_tokens_total{agent="support-bot",direction="input",model="model-a"} 100.0' in text


async def test_otlp_gzip_and_json_and_bad_payloads(client: httpx.AsyncClient) -> None:
    body, ctype = otlp_payload("hr-policy-qa")
    assert (
        await client.post(
            "/v1/traces",
            content=gzip.compress(body),
            headers={**INGEST, "content-type": ctype, "content-encoding": "gzip"},
        )
    ).status_code == 200

    doc = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
                              "scopeSpans": [{"spans": [{"traceId": "ab" * 16, "spanId": "cd" * 8, "name": "invoke_agent it-helpdesk", "kind": 2,
                                                         "startTimeUnixNano": "1760000000000000000", "endTimeUnixNano": "1760000000500000000",
                                                         "status": {"code": 2},
                                                         "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                                                                        {"key": "gen_ai.agent.name", "value": {"stringValue": "it-helpdesk"}}]}]}]}]}  # fmt: skip
    resp = await client.post(
        "/v1/traces", content=json.dumps(doc), headers={**INGEST, "content-type": "application/json"}
    )
    assert resp.status_code == 200
    detail = (await client.get(f"/v1/traces/{'ab' * 16}")).json()
    assert (
        detail["trace"]["has_error"]
        and detail["spans"][0]["agent"] == "it-helpdesk"
        and detail["spans"][0]["duration_ms"] == 500.0
    )

    assert (
        await client.post(
            "/v1/traces",
            content=b"\x00not otlp",
            headers={**INGEST, "content-type": "application/x-protobuf"},
        )
    ).status_code in (200, 400)
    assert (
        await client.post(
            "/v1/traces", content=b"{broken", headers={**INGEST, "content-type": "application/json"}
        )
    ).status_code == 400


async def test_trace_becomes_a_regression_case(client: httpx.AsyncClient) -> None:
    body, ctype = otlp_payload()
    await client.post("/v1/traces", content=body, headers={**INGEST, "content-type": ctype})
    trace_id = (await client.get("/v1/traces", params={"hours": 1})).json()[0]["trace_id"]
    resp = await client.post(
        f"/v1/traces/{trace_id}/to-dataset",
        json={"dataset": "incidents", "expected": "Boots: 30 days.", "tags": ["wrong-answer"]},
    )
    assert resp.status_code == 201
    case = resp.json()
    assert (
        case["id"] == f"incident-{trace_id[:8]}"
        and case["context"] == ["Footwear: 30 days."]
        and "incident" in case["tags"]
        and case["critical"]
    )
    assert (
        await client.post(f"/v1/traces/{trace_id}/to-dataset", json={})
    ).status_code == 409  # already captured
    exported = (await client.get("/v1/datasets/incidents/export")).text
    assert json.loads(exported.splitlines()[0])["expected"] == "Boots: 30 days."
    assert (await client.get("/v1/datasets/incidents/cases")).json()["total"] == 1


async def test_gate_runs_stay_out_of_health_and_trends(client: httpx.AsyncClient) -> None:
    await client.post("/v1/admin/sync")
    nightly = await make_run(model="sim/model-a", sample=10)
    await push(client, nightly)
    trial = await make_run(model="sim/model-b", sample=10)  # a candidate that never shipped
    trial.labels = {**trial.labels, "role": "candidate"}
    await push(client, trial)

    agent = (await client.get("/v1/agents/support-bot")).json()
    assert agent["latest_run"]["id"] == nightly.id
    trend = (await client.get("/v1/agents/support-bot/trend")).json()
    assert [p["run_id"] for p in trend] == [nightly.id]
    overview = (await client.get("/v1/overview")).json()
    row = next(a for a in overview["agents"] if a["agent"]["slug"] == "support-bot")
    assert row["latest_run"]["id"] == nightly.id
    assert (await client.get("/v1/runs", params={"agent": "support-bot"})).json()[
        "total"
    ] == 2  # still listed


async def test_traffic_aggregates_and_health(client: httpx.AsyncClient) -> None:
    await client.post("/v1/admin/sync")
    for _ in range(3):
        body, ctype = otlp_payload("support-bot")
        await client.post("/v1/traces", content=body, headers={**INGEST, "content-type": ctype})
    # distinct traces: each call above produces fresh trace ids
    buckets = (
        await client.get("/v1/agents/support-bot/traffic", params={"hours": 1, "bucket_min": 60})
    ).json()
    assert sum(b["requests"] for b in buckets) == 3 and sum(b["tokens_in"] for b in buckets) == 300
    agent = (await client.get("/v1/agents/support-bot")).json()
    assert (
        agent["traffic"]["requests"] == 3 and agent["status"] == "unknown"
    )  # live traffic but no evaluation yet
    assert (await client.get("/v1/agents/nope")).status_code == 404


async def test_models_leaderboard_and_pareto(client: httpx.AsyncClient) -> None:
    for model in ("sim/model-a", "sim/model-b", "sim/model-c"):
        await push(client, await make_run(model=model, sample=25))
    models = {m["model"]: m for m in (await client.get("/v1/models")).json()}
    assert set(models) == {"sim/model-a", "sim/model-b", "sim/model-c"}
    assert (
        models["sim/model-b"]["cost_per_1k_usd"]
        < models["sim/model-c"]["cost_per_1k_usd"]
        < models["sim/model-a"]["cost_per_1k_usd"]
    )
    assert (
        models["sim/model-b"]["on_pareto_front"] and models["sim/model-c"]["on_pareto_front"]
    )  # cheapest + best value


# --- uploaded folders ------------------------------------------------------------------------


def folder(name: str) -> dict[str, str]:
    """A folder as the dashboard uploads it: paths keep the dropped folder's own name."""
    root = ROOT / "examples" / name
    return {f"{name}/{p.name}": p.read_text(encoding="utf-8") for p in sorted(root.iterdir()) if p.is_file()}


@pytest.fixture
def uploads(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from pground_api.settings import get_settings

    monkeypatch.setenv("PGROUND_PACKS_DIR", str(tmp_path / "packs"))
    monkeypatch.setenv("PGROUND_ALLOW_CODE_UPLOADS", "true")
    get_settings.cache_clear()
    yield tmp_path / "packs"
    get_settings.cache_clear()


async def test_an_uploaded_folder_joins_the_fleet_and_is_judged(client: httpx.AsyncClient, uploads) -> None:
    from pground_api import queue, worker

    resp = await client.post("/v1/packs", json={"files": folder("my-first-bot")})
    assert resp.status_code == 201, resp.text
    pack = resp.json()
    assert pack["name"] == "my-first-bot" and pack["agents"] == ["shop-bot"] and pack["gate"] == "release"
    assert "bot.py" in pack["files"] and (uploads / "my-first-bot" / "provingground.yaml").is_file()
    claimed = await queue.claim("w")
    assert claimed is not None and claimed.payload["pack"] == "my-first-bot"
    await worker.execute(claimed, "w")
    job = (await client.get(f"/v1/jobs/{pack['jobs'][0]['id']}")).json()
    assert job["status"] == "succeeded", job["error"]
    assert job["result"]["verdict"] == "FAIL"  # both planted bugs: a wrong answer and a leaked secret
    gate = (await client.get(f"/v1/gates/evaluations/{job['result']['evaluation_id']}")).json()
    assert {r["verdict"] for r in gate["result"]["rules"]} == {"FAIL"}
    agent = (await client.get("/v1/agents/shop-bot")).json()
    assert agent["agent"]["team"] == "my-first-bot" and agent["latest_run"]["id"] == job["result"]["run_id"]
    assert agent["gate"]["verdict"] == "FAIL"  # an upload's run is a normal run: it shows in health

    # a job launched later from the dashboard finds the folder by itself
    again = (await client.post("/v1/jobs", json={"kind": "run", "payload": {"target": "shop-bot"}})).json()
    assert again["payload"]["pack"] == "my-first-bot"
    # re-syncing the demo config leaves uploaded agents alone
    await client.post("/v1/admin/sync")
    assert "shop-bot" in {a["slug"] for a in (await client.get("/v1/agents")).json()}
    # re-uploading replaces the folder; removing it takes its agents out of the fleet
    assert (await client.post("/v1/packs", json={"files": folder("my-first-bot")})).status_code == 201
    assert [p["name"] for p in (await client.get("/v1/packs")).json()] == ["my-first-bot"]
    assert (await client.delete("/v1/packs/my-first-bot")).status_code == 204
    assert (await client.get("/v1/agents/shop-bot")).status_code == 404
    assert not (uploads / "my-first-bot").exists()


async def test_uploads_that_cannot_be_trusted_or_run_are_refused(
    client: httpx.AsyncClient, uploads, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pground_api.settings import get_settings

    good = folder("my-first-bot")
    yaml_key = "my-first-bot/provingground.yaml"

    async def refused(files: dict[str, str], status: int = 422) -> str:
        resp = await client.post("/v1/packs", json={"files": files})
        assert resp.status_code == status, resp.text
        return resp.json()["detail"]

    assert "no provingground.yaml" in await refused(
        {"x/questions.jsonl": good["my-first-bot/questions.jsonl"]}
    )
    assert "bad file path" in await refused({**good, "../escape.py": "x = 1"})
    assert "providers" in await refused(
        {**good, yaml_key: good[yaml_key] + "providers:\n  evil: {base_url: 'http://evil'}\n"}
    )
    outside = good[yaml_key].replace("dataset: questions.jsonl", "dataset: ../../provingground.yaml")
    assert "inside the folder" in await refused({**good, yaml_key: outside})
    await client.post("/v1/admin/sync")  # the demo fleet owns support-bot
    taken = good[yaml_key].replace("shop-bot:", "support-bot:")
    assert "already exists" in await refused({**good, yaml_key: taken}, 409)

    monkeypatch.setenv("PGROUND_ALLOW_CODE_UPLOADS", "false")
    get_settings.cache_clear()
    assert "turned off" in await refused(good)
    assert not [p for p in uploads.iterdir() if not p.name.startswith(".")]  # nothing was installed
    assert not list(uploads.glob(".incoming-*"))  # and nothing was left behind


async def test_demo_off_starts_with_an_empty_fleet(
    client: httpx.AsyncClient, uploads, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pground_api.deps import project_config
    from pground_api.settings import get_settings

    await client.post("/v1/admin/sync")
    monkeypatch.setenv("PGROUND_DEMO", "false")
    get_settings.cache_clear()
    project_config.cache_clear()
    try:
        assert (await client.post("/v1/admin/sync")).json()["agents"] == 0
        assert (await client.get("/v1/agents")).json() == []  # the demo agents left the fleet
        assert (await client.get("/v1/config")).json()["targets"] == []
        assert (await client.post("/v1/packs", json={"files": folder("my-first-bot")})).status_code == 201
        assert [a["slug"] for a in (await client.get("/v1/agents")).json()] == ["shop-bot"]
    finally:
        monkeypatch.setenv("PGROUND_DEMO", "true")
        get_settings.cache_clear()
        project_config.cache_clear()
