"""Inspect AI bridge.

* ``pground/<target>`` becomes an Inspect model: any Inspect task (including `inspect_evals`
  benchmarks) can be run against a ProvingGround target - a raw model, a Python agent or an HTTP
  service - without the task knowing.
* The resulting ``EvalLog`` is imported as a regular ProvingGround ``Run`` so it can be
  compared, gated and reported like any other evaluation.

Install with ``pip install 'provingground[adapters]'``.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .metrics import summarize
from .models import (
    CaseResult,
    CaseStatus,
    Message,
    Run,
    Score,
    SuiteInfo,
    TargetOutput,
    TestCase,
    Usage,
    sha256_hex,
)
from .scorers.adapters.common import require
from .targets import InvokeContext, Target, build_target

require("inspect_ai")

from inspect_ai import Task, eval_async  # noqa: E402
from inspect_ai.log import EvalLog, EvalSample  # noqa: E402
from inspect_ai.model import (  # noqa: E402
    ChatMessage,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)

_ACTIVE: dict[str, Any] = {}  # set by run_inspect_task before Inspect constructs the model


def _to_platform_messages(messages: list[ChatMessage]) -> tuple[str | None, list[Message]]:
    system: list[str] = []
    out: list[Message] = []
    for m in messages:
        text = m.text
        if m.role == "system":
            system.append(text)
        elif m.role in ("user", "assistant"):
            out.append(Message(role=m.role, content=text))
        elif m.role == "tool":
            out.append(Message(role="tool", content=text, tool_call_id=getattr(m, "tool_call_id", None)))
    return ("\n".join(system) or None), out


@modelapi(name="pground")
class ProvingGroundAPI(ModelAPI):
    """Inspect model provider backed by a ProvingGround target (text in, text out)."""

    def __init__(self, model_name: str, base_url: str | None = None, api_key: str | None = None,
                 config: GenerateConfig | None = None, **kwargs: Any) -> None:  # fmt: skip
        super().__init__(model_name, base_url, api_key, [], config or GenerateConfig())
        cfg: ProjectConfig = _ACTIVE["cfg"]
        overrides = _ACTIVE.get("overrides") or {}
        target_cfg = cfg.target(model_name).with_overrides(overrides)
        self.target: Target = build_target(model_name, target_cfg, root=cfg.root)

    async def generate(
        self, input: list[ChatMessage], tools: Any, tool_choice: Any, config: GenerateConfig
    ) -> ModelOutput:
        system, messages = _to_platform_messages(input)
        case = TestCase(
            id=f"inspect-{uuid.uuid4().hex[:8]}", messages=messages or [Message(role="user", content="")]
        )
        if system and self.target.info.kind == "model":
            self.target.config.system_prompt = system
        out: TargetOutput = await self.target.invoke(case, InvokeContext(run_id="inspect", suite="inspect"))
        result = ModelOutput.from_content(model=self.model_name, content=out.text)
        result.usage = ModelUsage(
            input_tokens=out.usage.total_input_tokens,
            output_tokens=out.usage.output_tokens,
            total_tokens=out.usage.total_input_tokens + out.usage.output_tokens,
        )
        return result


def _normalize(value: Any) -> tuple[float | None, bool | None]:
    """Inspect score value -> (0..1 value, passed)."""
    if isinstance(value, bool):
        return (1.0 if value else 0.0), value
    if isinstance(value, int | float):
        v = float(value)
        return max(0.0, min(1.0, v)), v >= 0.5
    if isinstance(value, str):
        table = {"C": 1.0, "P": 0.5, "I": 0.0, "N": 0.0}
        if value in table:
            return table[value], value == "C"
    return None, None


def _sample_text(sample: EvalSample) -> str:
    return sample.output.completion if sample.output else ""


def import_eval_log(log: EvalLog, target: Target) -> Run:
    """Convert an Inspect ``EvalLog`` into a ProvingGround ``Run``."""
    info = target.info
    suite = f"inspect:{log.eval.task}"
    results: list[CaseResult] = []
    for i, sample in enumerate(log.samples or []):
        scores: list[Score] = []
        for name, sc in (sample.scores or {}).items():
            value, passed = _normalize(sc.value)
            scores.append(Score(metric=f"quality.inspect_{name}", value=value, passed=passed,
                                reason=(sc.explanation or "")[:300], scorer=f"inspect:{name}"))  # fmt: skip
        usage = Usage()
        for u in (sample.model_usage or {}).values():
            usage = usage + Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0)
        status = CaseStatus.ERROR if sample.error else CaseStatus.OK
        prompt = sample.input if isinstance(sample.input, str) else " ".join(m.text for m in sample.input)
        results.append(CaseResult(
            suite=suite, case_id=str(sample.id if sample.id is not None else i), input_preview=prompt[:500],
            expected=str(sample.target) if sample.target else None, status=status,
            output=TargetOutput(status=status, text=_sample_text(sample), usage=usage, latency_ms=(sample.total_time or 0) * 1000,
                                error=sample.error.message if sample.error else None),
            scores=scores,
        ))  # fmt: skip
    created = log.eval.created
    created_dt = datetime.fromisoformat(created) if isinstance(created, str) else datetime.now(UTC)
    return Run(
        id=f"run-inspect-{uuid.uuid4().hex[:10]}", created_at=created_dt, finished_at=datetime.now(UTC),
        target=info, labels={"source": "inspect", "inspect_task": log.eval.task, "inspect_version": str(log.version)},
        suites=[SuiteInfo(name=suite, dataset_version=sha256_hex([r.case_id for r in results])[:12], n_cases=len(results),
                          scorers=sorted({s.scorer for r in results for s in r.scores}))],
        results=results, summary=summarize(results),
    )  # fmt: skip


async def run_inspect_task(
    cfg: ProjectConfig,
    target_name: str,
    task: str | Task,
    *,
    limit: int | None = None,
    overrides: dict[str, object] | None = None,
    log_dir: Path | None = None,
) -> Run:
    """Run an Inspect task against a ProvingGround target and return the result as a Run."""
    os.environ.setdefault("INSPECT_DISPLAY", "none")  # no TUI: we render our own reports
    _ACTIVE.update(cfg=cfg, overrides=overrides)
    target = build_target(target_name, cfg.target(target_name).with_overrides(overrides or {}), root=cfg.root)
    try:
        logs = await eval_async(
            task, model=f"pground/{target_name}", limit=limit,
            log_dir=str(log_dir or tempfile.mkdtemp(prefix="pground-inspect-")), score=True,
        )  # fmt: skip
    finally:
        await target.aclose()
    if not logs or logs[0].status != "success":
        status = logs[0].status if logs else "no log"
        error = getattr(getattr(logs[0], "error", None), "message", "") if logs else ""
        raise RuntimeError(f"Inspect evaluation failed ({status}): {error}")
    return import_eval_log(logs[0], target)
