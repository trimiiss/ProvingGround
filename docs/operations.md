# Operations

## Monitoring live traffic

Agents export OpenTelemetry spans (GenAI semantic conventions) over OTLP/HTTP to
`POST /v1/traces`, authenticated with the ingest key. The API stores them, enriches them (agent,
model, tokens, cost) and derives Prometheus counters and histograms: requests, errors, latency,
tokens, cost and tool calls per agent and model.

`/metrics` also exposes gauges read from the database: latest evaluation scores, last gate verdict,
red-team attack success rate, online-eval scores and queue depth. `deploy/alerts.yml` holds 12
alert rules (latency, error rate, cost spike, score drop, failed gate, high attack success,
online faithfulness, live secret leaks, refusal spikes); Grafana has fleet and per-agent dashboards.

Set `PGROUND_TRACE_CONTENT=0` to keep prompts and answers out of traces entirely (online
evaluation and "add to dataset" then have nothing to work with).

## Online evaluation

Offline suites test what you thought of; live traffic is what users actually ask. Every five
minutes the `online-eval` schedule takes a deterministic 20% sample of the last hour's live
requests that have not been scored and runs the checks that need no expected answer:

| Metric | Check |
|---|---|
| `factuality.faithfulness`, `factuality.hallucination_rate` | claims in the answer vs the documents the agent retrieved for that request (recorded on the trace) |
| `safety.canary_leak_rate` | the agent's planted secrets in an answer |
| `safety.pii_leak_rate` | personal data the user did not provide |
| `quality.refusal_rate` | share of real requests refused |

Scores land in `online_scores`, appear on the agent's page and as
`pground_online_score{agent,metric}` (hourly mean). Agent health turns to *warning* when online
faithfulness drops below 80%; alerts fire on low faithfulness, any live leak, and refusal spikes.
Each trace is scored once, and sampling is by trace id, so overlapping windows and several
workers never double-count. Failed requests are left to the error-rate metrics.

## Schedules

Recurring jobs are declared in `provingground.yaml` and created on the first sync; after that the
database owns them, so pausing one through the API sticks:

```yaml
schedules:
  nightly-fleet:  {cron: "0 2 * * *",   job: run,         payload: {target: "*", use_cache: false}}
  weekly-redteam: {cron: "30 4 * * 1",  job: redteam,     payload: {target: "*", use_cache: false}}
  online-eval:    {cron: "*/5 * * * *", job: online_eval, payload: {rate: 0.2, hours: 1}}
```

`target: "*"` fans out to one job per fleet agent. The nightly full run is what catches *silent*
drift: a provider updating a model behind the same name shows up as a step in the trend chart with
no model-change marker. Manage schedules with `GET /v1/schedules`, `PUT /v1/schedules/{name}`,
`DELETE /v1/schedules/{name}`; all times are UTC.

## Using real models

Nothing requires a key. To use real models, add keys to `.env` (see `.env.example`) and change
model references in config: a target's `model:` (`anthropic/claude-opus-5-5`, `openai/gpt-…`,
`openrouter/…`, `ollama/llama3`, any OpenAI-compatible endpoint under `providers:`), or the
judge's. Then run `pground doctor` and a small `--sample 50` comparison before a full run: judge
calls dominate cost.

The `pground` CLI loads the nearest `.env` (the current directory or a parent); a variable already
set in the shell wins. The compose stack passes the provider keys into the API, worker and demo
agents, so after editing `.env` run `docker compose up -d` to recreate them. A `bot.py` in an
uploaded folder runs in the worker and can read those keys, one more reason to keep
`PGROUND_ALLOW_CODE_UPLOADS` off on a shared server.

## Running the database on Supabase

Supabase is managed PostgreSQL, so the API, worker, migrations and the job queue
(`SKIP LOCKED`, `LISTEN/NOTIFY`) run on it unchanged. Point `PGROUND_DATABASE_URL` at it:

```bash
PGROUND_DATABASE_URL=postgresql+asyncpg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?ssl=require
```

* Use the **session pooler** (port 5432) or the direct connection. The transaction pooler
  (port 6543) breaks the worker's `LISTEN` and asyncpg's prepared statements.
* Supabase publishes the `public` schema through its REST API. The platform's tables (runs,
  traces, API keys) should not be reachable that way: enable row-level security on them with no
  policies, or remove `public` from the exposed schemas in the project's API settings.
* Keep the local Postgres container for development, tests and CI: tests create and drop
  throwaway databases.

## Uploading agents from the dashboard

The **Upload agent** page takes a test folder: a `provingground.yaml` with its datasets and, for a
Python bot, its code. Each target in it joins the fleet as an agent (its team is the folder's
`project` name), is evaluated straight away, and gets a verdict from the folder's `release` gate.
Dropping a folder with the same `project` again replaces it; that is how you re-test after a fix.
`examples/upload-me` is a ready-made folder with three bots.

Folders are stored in `PGROUND_PACKS_DIR`, which the API and the worker must share (a volume in
`docker-compose.yml`). An upload is untrusted input, so it may not define `providers` (they would
receive the server's API keys), may not use `target_sources`, may read only `DEMO_AGENTS_URL` from
the environment, and must keep its datasets inside the folder. Python targets and generated suites
run code in the worker: they are accepted only with `PGROUND_ALLOW_CODE_UPLOADS=true`, which the
local compose stack sets and a shared server should not.

**Starting empty.** `PGROUND_DEMO=false` (in `.env`) leaves the demo company out: the fleet holds
only what you upload. To clear everything that is already stored:

```bash
docker compose down
docker volume rm provingground_pgdata provingground_promdata provingground_packs
docker compose up -d
```

Set `PGROUND_DEMO=true` (or remove the line) and run the seed command to bring the demo back.

## Configuration reference

| Variable | Default | Used by |
|---|---|---|
| `PGROUND_API_KEY` | `dev-admin-key` | API, dashboard, CLI writes |
| `PGROUND_INGEST_KEY` | `dev-ingest-key` | agents sending traces |
| `PGROUND_DATABASE_URL` | local compose Postgres | API, worker |
| `PGROUND_API_URL` | `http://localhost:8000` | CLI `push` / `api:` baselines, dashboard |
| `PGROUND_OTLP_ENDPOINT` | — | agents (where to send traces) |
| `PGROUND_TRACE_CONTENT` | `1` | agents (record prompts and answers on spans) |
| `PGROUND_SIM_TIME_SCALE` | `0` (compose demo: `0.25`) | simulated models: how much of the simulated latency to actually sleep |
| `PGROUND_DEMO` | `true` | API, worker: include the demo company's 20 agents |
| `PGROUND_PACKS_DIR` | `.provingground/packs` (compose: `/data/packs`) | API, worker: uploaded test folders |
| `PGROUND_ALLOW_CODE_UPLOADS` | `false` (compose: `true`) | API, worker: accept uploads that run Python code |
