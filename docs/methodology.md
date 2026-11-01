# Methodology

Getting a number out of an evaluation is easy; trusting it is the hard part. This page describes
the measures ProvingGround takes so a score means what it says, and why the release gate is a
statistical decision rather than a threshold on one average.

## 1. Eval hygiene

**Every case has an explicit status.** A target returns `ok`, `refused`, `truncated`, `timeout`,
`error` or `model_mismatch`. Infrastructure failures (`error`, `timeout`) are *not scored*: an
outage is not a wrong answer, and folding it in would let flaky infrastructure move quality
metrics. They show up in `reliability.error_rate` and `reliability.timeout_rate` instead, which
the gate also checks.

**Refusals are a status, not an error.** A provider-level refusal (e.g. Anthropic
`stop_reason: refusal`) counts as a successful defence in red-team suites and as a false refusal
in the over-refusal suite.

**The served model must be the requested one.** If the API reports a different model than the
one configured, the case is `model_mismatch`. Targets under test never use server-side
fallbacks; only the judge may fall back, and it records which model actually judged.

**Latency counts the final successful attempt only.** Retries with backoff are kept out of
latency so a rate limit is not mistaken for a slow model. Tokens and cost come from the API's
`usage` block, including prompt-cache reads and writes; judge cost is tracked separately.

**Everything is versioned.** A run records the dataset content hash per suite, each scorer's
version, the target fingerprint (model, prompts, parameters, code), the git commit and the judge
model. A summary is always recomputed from the raw per-case rows.

## 2. Comparing two runs

Both runs answer the same cases, so they are compared **pair by pair** (case and repeat), which
removes the variance between cases from the comparison and gives far tighter intervals than
comparing two averages.

* **Means and rates**: seeded paired bootstrap of the per-case differences, giving a 95%
  confidence interval for the change.
* **Pass/fail outcomes**: exact McNemar test on the discordant pairs (cases that flipped).
* **Latency percentiles**: bootstrap of the percentile itself.
* **Noise floor**: for each metric, the half-width of that interval: the smallest change this
  sample size can tell apart from noise. Reports state it next to every delta.

Before trusting any delta, the comparison checks that the runs are comparable and flags it if not:

| Check | Severity |
|---|---|
| A suite's dataset changed between runs | blocks |
| A scorer's version changed | blocks |
| Different suites were run | warns |
| Error rates differ a lot (the scored subsets differ) | warns |
| Response-cache hit share differs (latency/cost may be an artefact) | warns |

## 3. The gate

A policy is a list of rules over metrics (see `gates:` in `provingground.yaml`):

* **Absolute** (`min`, `max`): judged on the candidate's point estimate, e.g. attack success rate
  ≤ 5%.
* **Non-inferiority** (`max_drop`, `max_increase`, absolute or `"25%"` relative): the candidate
  may be worse than the baseline by at most the margin. The rule *passes* only if the whole
  confidence interval of the change is within the margin, *fails* if the whole interval is beyond
  it, and is **inconclusive** if the interval straddles it.
* **Critical cases** (`critical_cases: must_pass`): with a baseline, any critical case that
  passed before and fails now blocks; critical cases that were already failing are reported but
  do not block a change that did not cause them.

`on_inconclusive` decides what an inconclusive rule means (`fail` for releases: if the sample
cannot rule out a regression, do not ship). Each rule has a `severity`: `block` rules decide the
verdict, `warn` rules are reported only.

A rule on a metric that an agent does not measure (e.g. faithfulness for a JSON extractor) is
`N/A`. If the *baseline* measured it and the candidate no longer does, that is a coverage
regression and fails; `required: true` makes a missing metric fail outright.

The verdict is `PASS`, `WARN` (only warnings failed), or `FAIL`; the CLI exits 0, 0 and 1, and 2
for a pipeline error, so CI can tell "the model got worse" from "the evaluation broke".

## 4. Are the graders right?

`pground doctor` validates every grader before its scores are trusted:

* **Oracle check**: the gold answer must pass.
* **Null check**: junk answers must fail.
* **Known negatives**: answers with a planted fault must be caught.
* **Detector precision and recall**: the simulated models record which faults they injected
  (hallucinated numbers, leaked canaries, followed injections) as ground truth, which the
  hallucination and attack detectors are measured against. With the `[ml]` extra installed:
  NLI 1.00 / 1.00, SelfCheck 0.94 / 0.94, injection classifier 1.00 / 1.00 (precision / recall).

**About the offline judge.** The default judge is `sim/judge`, a heuristic that grades fact
recall, claim support and question relevance. It is labelled as such on every score. It is good
enough to make the platform's behaviour reproducible, not to grade a real model. For that,
configure a real judge (the documented default is `claude-opus-5-5`), run `pground doctor`, then
a small comparison before any full run. A warning fires when the judge and the target come from
the same model family (self-preference bias), and the judge prompt wraps candidate answers as
untrusted data, does not reward length, and never labels a response "reference" or "baseline".

## 5. Simulated models

`sim/model-a`, `sim/model-b` and `sim/model-c` are deterministic test doubles seeded by the model
and prompt, with a shared per-case difficulty so that faults correlate across models as they do
in practice:

| | quality | hallucination | attack resistance | latency | price |
|---|---|---|---|---|---|
| `model-a` premium | high | low | strong | slow | high |
| `model-b` budget | lower | ~3× A | weak | fast | lowest |
| `model-c` balanced | ≈ A | ≈ A | ≈ A | fast | mid |

They exist so that every test, demo and CI run is free and reproducible. They are not a model of
any real model's behaviour.
