from __future__ import annotations

import asyncio

from provingground.datasets import LoadedSuite
from provingground.models import CaseStatus, TargetInfo, TargetOutput, TestCase
from provingground.runner import RunSettings, execute_run
from provingground.scorers import SimJudge, build_scorer
from provingground.targets.base import InvokeContext, Target, TargetError


class ScriptedTarget(Target):
    """A target whose behaviour per case is dictated by case.metadata['behaviour']."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    @property
    def info(self) -> TargetInfo:
        return TargetInfo(name="scripted", kind="python", fingerprint="scripted", model="sim/model-a")

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        self.calls[case.id] = self.calls.get(case.id, 0) + 1
        behaviour = case.metadata.get("behaviour")
        if behaviour == "hang":
            await asyncio.sleep(5)
        if behaviour == "transient":
            if self.calls[case.id] <= 2:
                raise TargetError("temporary", transient=True)
            return TargetOutput(text="recovered", model_served="sim/model-a")
        if behaviour == "fatal":
            raise TargetError("permanent", transient=False)
        if behaviour == "mismatch":
            return TargetOutput(text="x", model_served="sim/model-b", model_requested="sim/model-a")
        return TargetOutput(text=case.expected or "ok", model_served="sim/model-a", latency_ms=10.0)


def suite(cases: list[TestCase]) -> LoadedSuite:
    return LoadedSuite(
        name="s", cases=cases, scorers=[build_scorer("exact_match")], dataset=None, dataset_version="v1"
    )


async def run(cases: list[TestCase], **kw: object):
    settings = RunSettings(use_cache=False, timeout_s=1.0, retries=2, backoff_base_s=0.0, n_boot=0, **kw)
    return await execute_run(ScriptedTarget(), [suite(cases)], settings, SimJudge())


async def test_timeout_is_reported_not_scored() -> None:
    r = await run([TestCase(id="t", input="q", expected="ok", metadata={"behaviour": "hang"})])
    result = r.results[0]
    assert result.status == CaseStatus.TIMEOUT
    assert result.passed is None  # a timeout is never scored as a wrong answer
    assert result.scores == []


async def test_fatal_error_status() -> None:
    r = await run([TestCase(id="e", input="q", expected="ok", metadata={"behaviour": "fatal"})])
    assert r.results[0].status == CaseStatus.ERROR
    assert r.summary.metrics["reliability.error_rate"].mean == 1.0


async def test_transient_error_retried_then_succeeds() -> None:
    r = await run([TestCase(id="r", input="q", expected="recovered", metadata={"behaviour": "transient"})])
    assert r.results[0].status == CaseStatus.OK
    assert r.results[0].output.attempts == 3
    assert r.results[0].passed is True


async def test_model_mismatch_detected() -> None:
    r = await run([TestCase(id="m", input="q", expected="x", metadata={"behaviour": "mismatch"})])
    assert r.results[0].status == CaseStatus.MODEL_MISMATCH
    assert r.results[0].passed is None


async def test_repeats_and_summary() -> None:
    cases = [TestCase(id=f"c{i}", input="q", expected="ok") for i in range(3)]
    r = await run(cases, repeats=2)
    assert r.summary.n_results == 6
    assert r.summary.n_cases == 3
    assert r.summary.pass_rate == 1.0
