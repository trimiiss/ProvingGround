"""The async evaluation runner.

For every (suite, case, repeat) the runner invokes the target under a semaphore, retries
transient failures with backoff, enforces a hard per-case timeout, records an explicit status,
captures in-process spans, and scores only results whose status is ``ok`` or ``refused``.
"""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry.trace import SpanKind

from .cache import ResponseCache
from .datasets import LoadedSuite
from .metrics import summarize
from .models import (
    SCORABLE_STATUSES,
    CaseResult,
    CaseStatus,
    GitInfo,
    Run,
    Score,
    SuiteInfo,
    TargetInfo,
    TargetOutput,
    TestCase,
    sha256_hex,
)
from .providers import same_model
from .runtime_context import current_attempt, current_case_id
from .scorers import Judge, ScoreContext, Scorer
from .targets import InvokeContext, Target
from .tracing import collector, get_tracer, setup_tracing

ProgressFn = Callable[[int, int], None]
CACHEABLE_STATUSES = frozenset({CaseStatus.OK, CaseStatus.REFUSED, CaseStatus.TRUNCATED})


@dataclass
class RunSettings:
    repeats: int = 1
    sample: int | None = None
    seed: int = 0
    concurrency: int = 8
    timeout_s: float = 60.0
    retries: int = 2
    backoff_base_s: float = 0.5
    use_cache: bool = True
    cache_dir: Path | None = None
    n_boot: int = 1000

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("cache_dir", None)
        return data


def new_run_id(fingerprint: str) -> str:
    return f"run-{datetime.now(UTC):%Y%m%d-%H%M%S}-{fingerprint[:6]}-{secrets.token_hex(2)}"


async def _invoke_with_retries(
    target: Target, case: TestCase, ctx: InvokeContext, settings: RunSettings
) -> TargetOutput:
    started = time.perf_counter()
    rng = random.Random(f"{settings.seed}:{case.id}:{ctx.repeat}")
    last_error: BaseException | None = None
    attempts = 0
    for attempt in range(settings.retries + 1):
        attempts += 1
        token = current_attempt.set(attempt)
        attempt_started = time.perf_counter()
        try:
            output = await target.invoke(case, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if not getattr(exc, "transient", False) or attempt == settings.retries:
                break
            delay = min(8.0, settings.backoff_base_s * 2**attempt) * (0.5 + rng.random())
            await asyncio.sleep(delay)
            continue
        finally:
            current_attempt.reset(token)
        if output.latency_ms is None:
            output.latency_ms = (time.perf_counter() - attempt_started) * 1000
        output.attempts = attempts
        output.wall_ms = (time.perf_counter() - started) * 1000
        return output
    return TargetOutput(
        status=CaseStatus.ERROR,
        error=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error",
        attempts=attempts,
        wall_ms=(time.perf_counter() - started) * 1000,
    )


async def score_case(
    case: TestCase, output: TargetOutput, scorers: list[Scorer], ctx: ScoreContext
) -> list[Score]:
    if output.status not in SCORABLE_STATUSES:
        return []
    scores: list[Score] = []
    for scorer in scorers:
        if not scorer.applies(case, output):
            continue
        try:
            scores.extend(await scorer.score(case, output, ctx))
        except Exception as exc:
            metric = scorer.metrics[0] if scorer.metrics else f"scorer.{scorer.name}"
            scores.append(
                scorer.make(metric, None, None, f"scorer error: {type(exc).__name__}: {exc}", error=True)
            )
    return scores


async def _run_case(
    target: Target,
    info: TargetInfo,
    suite: LoadedSuite,
    case: TestCase,
    repeat: int,
    run_id: str,
    settings: RunSettings,
    judge: Judge,
    cache: ResponseCache | None,
) -> CaseResult:
    tracer = get_tracer("provingground.runner")
    attributes: dict[str, str | int] = {
        "pground.suite": suite.name, "pground.case_id": case.id, "pground.repeat": repeat,
        "pground.target": info.name, "pground.run_id": run_id,
    }  # fmt: skip
    with tracer.start_as_current_span("pground.case", kind=SpanKind.INTERNAL, attributes=attributes) as span:
        trace_id = format(span.get_span_context().trace_id, "032x")
        token = current_case_id.set(case.id)
        try:
            cache_key = sha256_hex([info.fingerprint, case.content_hash(), repeat]) if cache else None
            output = cache.get(cache_key) if cache and cache_key else None
            if output is not None:
                output.cached = True
            else:
                ctx = InvokeContext(run_id=run_id, suite=suite.name, repeat=repeat, timeout_s=settings.timeout_s,
                                    seed=settings.seed)  # fmt: skip
                try:
                    async with asyncio.timeout(settings.timeout_s):
                        output = await _invoke_with_retries(target, case, ctx, settings)
                except TimeoutError:
                    output = TargetOutput(
                        status=CaseStatus.TIMEOUT, error=f"exceeded {settings.timeout_s:g}s"
                    )
                output.trace_id = output.trace_id or trace_id
                captured = collector.pop(trace_id)
                if captured and not output.spans:
                    output.spans = captured
                if cache and cache_key and output.status in CACHEABLE_STATUSES:
                    cache.put(cache_key, output)
            if (
                output.status == CaseStatus.OK
                and info.model
                and output.model_served
                and not same_model(info.model, output.model_served)
            ):
                output.status = CaseStatus.MODEL_MISMATCH
                output.error = f"requested {info.model}, served {output.model_served}"
            span.set_attribute("pground.status", str(output.status))
            scores = await score_case(
                case, output, suite.scorers, ScoreContext(target=info, judge=judge, suite=suite.name)
            )
        finally:
            current_case_id.reset(token)
    return CaseResult(
        suite=suite.name,
        case_id=case.id,
        repeat=repeat,
        tags=case.tags,
        critical=case.critical,
        input_preview=case.prompt_text[:500],
        expected=case.expected,
        metadata={
            k: v for k, v in case.metadata.items() if k in ("attack", "answerable", "category", "fact_id")
        },
        status=output.status,
        output=output,
        scores=scores,
    )


async def execute_run(
    target: Target,
    suites: list[LoadedSuite],
    settings: RunSettings,
    judge: Judge,
    *,
    project: str | None = None,
    labels: dict[str, str] | None = None,
    git: GitInfo | None = None,
    progress: ProgressFn | None = None,
) -> Run:
    setup_tracing()
    info = target.info
    created = datetime.now(UTC)
    run_id = new_run_id(info.fingerprint)
    cache = ResponseCache(settings.cache_dir) if settings.use_cache and info.cacheable else None
    semaphore = asyncio.Semaphore(settings.concurrency)
    jobs = [(suite, case, rep) for suite in suites for case in suite.cases for rep in range(settings.repeats)]
    done = 0

    async def one(suite: LoadedSuite, case: TestCase, rep: int) -> CaseResult:
        nonlocal done
        async with semaphore:
            result = await _run_case(target, info, suite, case, rep, run_id, settings, judge, cache)
        done += 1
        if progress:
            progress(done, len(jobs))
        return result

    results = list(await asyncio.gather(*(one(*job) for job in jobs)))
    scorer_versions = {s.name: s.version for suite in suites for s in suite.scorers}
    return Run(
        id=run_id,
        created_at=created,
        finished_at=datetime.now(UTC),
        project=project,
        target=info,
        suites=[
            SuiteInfo(
                name=s.name,
                dataset=s.dataset,
                dataset_version=s.dataset_version,
                n_cases=len(s.cases),
                scorers=[sc.ref for sc in s.scorers],
            )
            for s in suites
        ],
        scorer_versions=scorer_versions,
        judge_model=judge.model if any(sc.uses_judge for s in suites for sc in s.scorers) else None,
        settings=settings.public(),
        git=git,
        labels=labels or {},
        results=results,
        summary=summarize(results, n_boot=settings.n_boot, seed=settings.seed),
    )
