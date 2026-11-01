# Adopting the gate in another repository

The quality gate is a composite GitHub Action. Any repository with AI agents can block pull
requests that make them worse.

## 1. Describe your agents

Add a `provingground.yaml` at the repository root (`pground init` writes a starter):

```yaml
project: my-company

judge:
  model: anthropic/claude-opus-5-5     # or sim/judge to start offline

targets:
  support-bot:
    kind: http                         # or: model (a raw LLM) / python (module:callable)
    url: https://staging.example.com/chat
    request: {message: "{{input}}"}   # JSON template; {{input}}, {{messages}}, {{context}} are filled per case
    response: {text: reply}           # dotted paths into the JSON reply
    canaries: ["INTERNAL-REF-7F3A"]    # plant this in the system prompt; leaking it is a failure
    suites: [support-quality, security-rag]

suites:
  support-quality:
    dataset: evals/support-quality.jsonl
    scorers: [correctness, relevance, faithfulness]
  security-rag:
    dataset: evals/security.jsonl
    scorers: [attack_success]

gates:
  release:
    baseline: rerun:origin/main        # rebuild main in a git worktree and run it too
    on_inconclusive: fail
    rules:
      - {metric: quality.correctness, min: 0.80, max_drop: 0.02}
      - {metric: factuality.faithfulness, min: 0.90, max_drop: 0.02}
      - {metric: safety.attack_success_rate, max: 0.02}
      - {metric: perf.latency_p95_ms, max_increase: 25%, severity: warn}
      - {metric: cost.per_1k_requests_usd, max_increase: 10%, severity: warn}
      - {critical_cases: must_pass}
```

Datasets are JSONL, one case per line:
`{"id": "...", "input": "...", "expected": "...", "context": ["..."], "tags": [], "critical": false}`.
Production failures can be added from the dashboard's trace view ("Add to dataset").

## 2. Add the workflow

```yaml
# .github/workflows/quality-gate.yml
name: quality-gate
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write          # for the sticky comment
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with: {fetch-depth: 0}  # the gate diffs against the base branch
      - uses: <owner>/provingground/.github/actions/provingground-gate@main
        with:
          base: origin/${{ github.base_ref }}
          policy: release
          sample: 100           # cases per suite on PRs; run the full suites on main / nightly
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The action installs ProvingGround with `install-command` (default `uv sync --frozen`, so add
`provingground` to your project's dependencies, or pass e.g. `pip install provingground`).
It fingerprints every target (model, prompts, parameters, code) on both sides and
evaluates only the targets the PR changed. It posts or updates one PR comment, writes the job
summary, uploads the HTML report, and exits 1 on a failed gate.

## 3. Make it required

In **Settings → Branches → main → Require status checks**, add `quality-gate`. A failing gate now
blocks the merge.

To also guard deploys, run the gate as a job that the deploy job `needs:` (see
`.github/workflows/deploy.yml`): when the gate fails, the deploy job never starts.

## Baselines

| `baseline:` | Meaning |
|---|---|
| `rerun:<ref>` | Check out `<ref>` in a worktree and run it now. Most honest; costs a second run (responses are cached). |
| `file:<path>` | A results JSON saved by a previous run (e.g. restored from `actions/cache` by `baseline.yml`). |
| `run:<id>` | A run stored locally in `.provingground/`. |
| `api:<agent>@<ref>` | The latest run pushed to the platform with label `ref=<ref>`. |

## Keeping PR runs cheap

* `sample:` on PRs, full suites on pushes to main and nightly.
* The response cache makes the baseline side free when main has not changed.
* Deterministic scorers run first; judge calls are cached by (model, prompt version, payload).
* Gate only changed targets (the default); `all-targets: true` gates everything.
