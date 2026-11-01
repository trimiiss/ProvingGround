from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from provingground.config import TargetConfig
from provingground.datasets import LoadedSuite
from provingground.models import CaseStatus, TargetInfo, TargetOutput, TestCase
from provingground.runner import RunSettings, execute_run
from provingground.scorers import SimJudge, build_scorer
from provingground.targets import build_target
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


async def test_python_target_imports_a_module_next_to_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bot.py beside provingground.yaml loads without PYTHONPATH (the first thing a newcomer tries)."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "pg_fixture_bot.py").write_text(
        "class Bot:\n"
        "    async def invoke(self, case, ctx):\n"
        "        return 'echo: ' + case.input\n"
        "\n"
        "def make_bot():\n"
        "    return Bot()\n",
        encoding="utf-8",
    )
    try:
        target = build_target("bot", TargetConfig(kind="python", entrypoint="pg_fixture_bot:make_bot"),
                              root=tmp_path)  # fmt: skip
        out = await target.invoke(TestCase(id="a", input="hi"), InvokeContext(run_id="r", suite="s"))
    finally:
        sys.modules.pop("pg_fixture_bot", None)
    assert out.text == "echo: hi"
    assert sys.path[-1] == str(tmp_path)  # appended: it never shadows an installed package


def test_editing_the_bot_changes_its_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise the response cache replays the old code's answers after a fix."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = tmp_path / "pg_fixture_edit.py"
    module.write_text("class Bot:\n    pass\n\ndef make_bot():\n    return Bot()\n", encoding="utf-8")
    cfg = TargetConfig(kind="python", entrypoint="pg_fixture_edit:make_bot")
    try:
        before = build_target("bot", cfg, root=tmp_path).info.fingerprint
        module.write_text(module.read_text(encoding="utf-8") + "# fixed\n", encoding="utf-8")
        after = build_target("bot", cfg, root=tmp_path).info.fingerprint
    finally:
        sys.modules.pop("pg_fixture_edit", None)
    assert before != after
