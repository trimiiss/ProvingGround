# ProvingGround

**An evaluation, red-teaming and reliability platform for a fleet of AI agents.** It tests every agent for answer quality, factual accuracy, security, latency and cost. It turns "can we switch Model A to Model B?" into a statistical yes/no. It also blocks the deploy in CI when the answer is no.

![Fleet overview](docs/screenshots/fleet.png)

Everything runs offline. The demo company ("Acme Outfitters", 20 agents) runs on deterministic simulated models and local PyTorch judges, so the whole stack (tests, dashboard, CI gate) works without an API key. Real providers (Anthropic, and any OpenAI-compatible endpoint) are implemented and are a config line away.

## What it does

| | |
|---|---|
| **Automated LLM testing** | 1,253 test cases across quality, RAG factuality, extraction, over-refusal and security suites. Every case has an explicit status (`ok / error / timeout / truncated / refused / model_mismatch`), and errors are never scored as wrong answers. |
| **Hallucination detection** | Claim-by-claim faithfulness against the retrieved documents, NLI entailment (DeBERTa), SelfCheck consistency across repeats, and a lexical grounding check for numbers and names. `pground doctor` measures each detector's precision and recall against known faults. |
| **Prompt injection and adversarial attacks** | A static campaign crosses goals × techniques × encodings (328 attacks), and an adaptive attacker learns from the target's replies. Success is decided by deterministic detectors: a planted canary leaks, a forbidden tool call happens, or an injected marker appears. |
| **Model comparison** | Paired, case-by-case comparison with bootstrap confidence intervals, McNemar tests, noise floors, and a quality-vs-cost Pareto front. |
| **Regression testing and CI/CD gates** | Policy-as-code rules: absolute thresholds, non-inferiority margins, and critical cases that must pass. Exit code 1 blocks the merge and the deploy job never runs. Only the agents a PR changed are evaluated. |
| **Tracing and monitoring** | OpenTelemetry GenAI spans over OTLP, a trace explorer with an "add to dataset" button, Prometheus metrics, alert rules and Grafana dashboards. |
| **Online evaluation** | A sampled share of live traffic is scored without reference answers: faithfulness to the retrieved documents, secret and PII leaks, refusals. Drift alerts fire on the hourly means. |
| **Reports** | Markdown, HTML and JSON for every run, comparison and gate decision, plus a sticky PR comment. |

## Model A → Model B on a pull request

A PR changes one line in `services/demo_agents/agents.yaml`: `support-bot.model: sim/model-a → sim/model-b`.

1. The `quality-gate` workflow fingerprints every agent and finds that only `support-bot` changed.
2. It rebuilds `origin/main` in a git worktree as the baseline and runs both versions on the same 825 cases.
3. It compares them pair by pair and applies the `release` policy:

| Rule | A (main) | B (PR) | Verdict |
|---|---|---|---|
| Faithfulness ≥ 0.85, max drop 0.03 | 0.952 | 0.816 | ❌ FAIL (Δ −0.136, CI −0.180…−0.096) |
| Hallucination rate ≤ 0.12 | 0.048 | 0.184 | ❌ FAIL |
| Correctness ≥ 0.75, max drop 0.03 | 0.865 | 0.728 | ❌ FAIL |
| Attack success rate ≤ 0.05 | 0.9% | 54.2% | ❌ FAIL |
| p95 latency, max +30% (warn) | 2.28 s | 1.03 s | ✅ |
| Cost per 1k requests, max +25% (warn) | $1.24 | $0.055 | ✅ |
| Critical cases must pass | — | — | ❌ `cq-refunds-timing-00` regressed |

4. The job exits 1. The PR gets a comment with the table and the worst regressions side by side, the merge is blocked, and `deploy.yml` never reaches its deploy job.

The same PR with `sim/model-c` passes: quality stays within margin, latency drops 56% and cost drops 77%.

![A blocked release](docs/screenshots/gate-blocked.png)

## Quickstart

Requirements: Docker, plus Python 3.12 with [uv](https://docs.astral.sh/uv/) for the CLI.

```bash
# the whole platform: API, worker, dashboard, demo agents, Postgres, Prometheus, Grafana
docker compose up -d --build
docker compose --profile tools run --rm seed          # 30 days of history for 20 agents (~5 min)
docker compose --profile traffic up -d traffic        # optional: live demo traffic
```

| | |
|---|---|
| Dashboard | http://localhost:3000 |
| API and OpenAPI docs | http://localhost:8000/docs |
| Grafana (anonymous viewer) | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

The CLI works without the platform:

```bash
uv sync
uv run pground run  --target support-bot --suite customer-quality,customer-factuality,security-rag
uv run pground gate --target support-bot --set model=sim/model-b      # exits 1
uv run pground gate --target support-bot --set model=sim/model-c      # exits 0
uv run pground attack --target support-bot --set model=sim/model-b    # adaptive red team
uv run pground doctor                                                 # are the graders right?
uv run python scripts/simulate_pr.py                                  # the PR flow, locally
```

No `.env` is needed. `.env.example` lists the optional settings: real LLM keys, passwords, Langfuse.

## Architecture

```mermaid
flowchart LR
  subgraph CI["GitHub Actions"]
    PR["PR / push / schedule"] --> CLI["pground CLI<br/>gate-changed"]
  end
  subgraph Core["provingground (Python)"]
    T["targets<br/>model · python · http"] --> R["async runner"] --> S["scorers<br/>deterministic · judge · NLI · adapters"]
    S --> ST["stats · compare · gate · reports"]
    RT["red team<br/>static + adaptive"] --> R
  end
  CLI --> Core
  subgraph Platform
    API["FastAPI<br/>REST · OTLP ingest · /metrics"] <--> DB[("PostgreSQL<br/>runs · scores · spans · jobs")]
    W["worker<br/>queue · schedules · online eval"] <--> DB
    W --> Core
  end
  Agents["agents under test"] -- "OTLP traces" --> API
  Core -- "invoke + traceparent" --> Agents
  CLI -- "push / api: baselines" --> API
  Web["Next.js dashboard"] --> API
  Prom["Prometheus → Grafana, alerts"] --> API
```

* **`packages/core`**: the engine and the `pground` CLI. Typed contracts (pydantic), an async runner with retries and timeouts, scorers, statistics, the gate, the red team and the reports. It works standalone and in CI.
* **`services/api`**: FastAPI and SQLAlchemy on PostgreSQL with Alembic migrations. A job queue on `FOR UPDATE SKIP LOCKED` with `LISTEN/NOTIFY`, cron schedules, OTLP/HTTP trace ingest (protobuf and JSON), and Prometheus metrics.
* **`services/demo_agents`**: the fictional retailer. Four runtimes (RAG Q&A, tool agent, JSON extractor, summarizer) back 20 agents, each with its own policies, planted canaries and model.
* **`web`**: the Next.js 16 dashboard (React 19, Tailwind, shadcn/ui, Recharts). It uses a client generated from the OpenAPI schema, and the admin key never leaves the server.
* **`deploy`**: Prometheus scrape config, 12 alert rules, and provisioned Grafana dashboards.

## How the listed tools fit

| Tool | Role here |
|---|---|
| PyTorch | Local judges with no API key: NLI claim entailment, SelfCheck, injection and toxicity classifiers (`[ml]` extra) |
| DeepEval, Ragas | Adapters: their metrics run as ProvingGround scorers, driven by the platform's judge (`[adapters]` extra) |
| Inspect AI | Inspect tasks and `inspect_evals` benchmarks run against any target; their logs are imported as runs |
| Langfuse | Optional OTLP export with eval scores attached to traces |
| OpenTelemetry | GenAI semantic-convention spans, `traceparent` propagated into agents, OTLP ingest |
| Prometheus, Grafana | Request, latency, token and cost metrics from traces; gauges from the database; alerts; dashboards |
| PostgreSQL | The system of record and the job queue |
| FastAPI, Next.js, GitHub Actions | API and ingest, dashboard, PR gate, gated deploy, a reusable gate action |

## Documentation

* [Methodology](docs/methodology.md): eval hygiene, the statistics behind the gate, and how the graders are validated.
* [Red teaming](docs/red-teaming.md): the goal × technique × converter taxonomy, the detectors, and the adaptive attacker.
* [Adopting the gate](docs/adopting-the-gate.md): add the quality gate to another repository.
* [Operations](docs/operations.md): online evaluation, schedules, alerts, real models, and running on Supabase.

## Screenshots

| | |
|---|---|
| ![Agent](docs/screenshots/agent.png) | ![Compare](docs/screenshots/compare.png) |
| Agent detail: trends with model changes, live traffic, red team, gate history | Compare: release decision, deltas with CIs, flipped cases |
| ![Red team](docs/screenshots/redteam.png) | ![Case](docs/screenshots/case-drawer.png) |
| Red team: technique × goal heatmap, leaked canaries highlighted | A failing case: input, retrieved context, output, every score with its reason |

## Honest limits

* The simulated models are test doubles. They make the platform's behaviour reproducible, but their failure modes are simpler than a real model's. Templated injections in particular are easier to detect than real ones.
* The offline judge is a heuristic and labels itself `sim/judge` in every score. Use a real judge (for example `claude-opus-5-5`) for scoring quality that matters, and run `pground doctor` after switching.
* It is single-tenant with API-key auth. Put the dashboard behind your own SSO before exposing it.

## Development

```bash
uv sync --all-extras                 # add the ML judges and framework adapters
uv run pytest                        # API tests use the compose Postgres: docker compose up -d postgres
uv run ruff check . && uv run mypy packages/core/src services/api/src services/demo_agents/src
cd web && npm ci && npm run lint && npm run build
PG_WEB_URL=http://localhost:3000 npx playwright test       # needs the seeded stack
```
