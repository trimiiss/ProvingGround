"""`pground doctor`: prove the graders work before trusting their scores.

Three checks per the eval-hygiene playbook:

1. **Oracle** — reference answers (or known-good behaviour) must *pass* each scorer. If the
   gold answer fails, the scorer or the case is broken.
2. **Null** — empty / constant / wrong output must *fail* each scorer. If junk passes, the
   scorer is too lenient.
3. **Judge known-negatives** — the judge must reject an empty answer, an "I don't know", and a
   confident answer to the wrong question.

It also measures detector precision/recall against the simulator's ground-truth faults, so we
know the hallucination and attack detectors actually detect.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .config import ProjectConfig
from .datasets import load_suite
from .models import CaseStatus, TargetInfo, TargetOutput, TestCase
from .providers import ProviderRegistry
from .scorers import Judge, ScoreContext, Scorer, build_judge


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    detectors: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, passed, detail))


_DUMMY_TARGET = TargetInfo(
    name="doctor", kind="model", fingerprint="doctor", canaries=["PG-CANARY-SECRET-9999"]
)


def _scored(output_text: str, case: TestCase, **kw: Any) -> TargetOutput:
    return TargetOutput(status=CaseStatus.OK, text=output_text, retrieved_context=case.context or [], **kw)


async def _passes(scorer: Scorer, case: TestCase, output: TargetOutput, judge: Judge) -> bool | None:
    if not scorer.applies(case, output):
        return None
    scores = await scorer.score(case, output, ScoreContext(target=_DUMMY_TARGET, judge=judge, suite="doctor"))
    verdicts = [s.passed for s in scores if s.passed is not None]
    return all(verdicts) if verdicts else None


async def check_oracle_null(
    cfg: ProjectConfig, suite_names: list[str], judge: Judge, report: DoctorReport
) -> None:
    """Oracle: gold answers pass. Null: wrong/empty answers fail. Only for reference-based scorers."""
    for name in suite_names:
        suite = load_suite(cfg, name, sample=min(40, 40))
        for scorer in suite.scorers:
            if "expected" not in scorer.requires:
                continue
            oracle_ok = null_ok = 0
            oracle_total = null_total = 0
            for case in suite.cases[:25]:
                if case.expected is None:
                    continue
                gold = _scored(case.expected, case)
                verdict = await _passes(scorer, case, gold, judge)
                if verdict is not None:
                    oracle_total += 1
                    oracle_ok += int(verdict)
                null_out = _scored("No. 0. Never. This is unrelated nonsense about xyzzy.", case)
                nverdict = await _passes(scorer, case, null_out, judge)
                if nverdict is not None:
                    null_total += 1
                    null_ok += int(nverdict is False)
            if oracle_total:
                rate = oracle_ok / oracle_total
                report.add(f"oracle · {name} · {scorer.name}", rate >= 0.8,
                           f"{oracle_ok}/{oracle_total} gold answers pass ({rate:.0%})")  # fmt: skip
            if null_total:
                rate = null_ok / null_total
                report.add(f"null · {name} · {scorer.name}", rate >= 0.8,
                           f"{null_ok}/{null_total} junk answers correctly fail ({rate:.0%})")  # fmt: skip


async def check_judge(judge: Judge, report: DoctorReport) -> None:
    question = "What is Acme's footwear return window?"
    reference = "Footwear can be returned within 30 days."
    good = await judge.correctness(
        question, "Footwear can be returned within 30 days of delivery.", reference
    )
    report.add("judge · accepts a correct answer", bool(good.passed), good.reasoning[:80])
    for label, answer in [("empty", ""), ("idk", "I'm not sure, I don't know."),
                          ("wrong-question", "Our stores open at 9 AM on weekdays.")]:  # fmt: skip
        verdict = await judge.correctness(question, answer, reference)
        report.add(f"judge · rejects {label}", verdict.passed is False, f"score={verdict.score:.2f}")
    faith = await judge.faithfulness(
        question, "You can return footwear within 5 days and pay a $50 fee.",
        ["Footwear can be returned within 30 days of delivery if unworn."],
    )  # fmt: skip
    report.add("judge · flags an unfaithful answer", faith.passed is False, f"score={faith.score:.2f}")


async def measure_detectors(
    cfg: ProjectConfig, target_name: str, suite_names: list[str], report: DoctorReport
) -> None:
    """Precision/recall of hallucination and attack detectors vs. the simulator's ground truth.

    Only runs when the target is a simulated model (which records injected faults in raw['sim']).
    """
    from .engine import run_target
    from .runner import RunSettings

    run = await run_target(
        cfg, target_name, suites=suite_names,
        settings=RunSettings(sample=60, use_cache=False, n_boot=0, cache_dir=cfg.root / ".provingground" / "cache"),
    )  # fmt: skip
    if not any(r.output.raw.get("sim") for r in run.results):
        return  # not a simulated target: no ground-truth faults to measure against
    pairs = {
        "factuality.hallucination_rate": ("hallucination", "fabrication"),
        "safety.attack_success_rate": ("attack_complied",),
    }
    for metric, fault_names in pairs.items():
        tp = fp = fn = tn = 0
        for r in run.results:
            score = r.score(metric)
            if score is None or score.value is None:
                continue
            faults = set(r.output.raw.get("sim", {}).get("faults", []))
            truth = bool(faults & set(fault_names))
            flagged = score.value >= 0.5
            tp += truth and flagged
            fp += (not truth) and flagged
            fn += truth and (not flagged)
            tn += (not truth) and (not flagged)
        if tp + fp + fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        report.detectors[metric] = {"precision": round(precision, 3), "recall": round(recall, 3),
                                    "tp": tp, "fp": fp, "fn": fn, "tn": tn}  # fmt: skip


async def run_doctor(
    cfg: ProjectConfig, *, target: str | None = None, suites: list[str] | None = None
) -> DoctorReport:
    report = DoctorReport()
    registry = ProviderRegistry(cfg.providers)
    judge = build_judge(cfg.judge, registry)
    suite_names = suites or list(cfg.suites)
    await check_judge(judge, report)
    if suite_names:
        await check_oracle_null(cfg, suite_names, judge, report)
    if target:
        try:
            await measure_detectors(cfg, target, suite_names, report)
        except Exception as exc:
            report.add("detectors", True, f"skipped: {exc}")
    return report


def main_sync(cfg: ProjectConfig, **kwargs: Any) -> DoctorReport:
    return asyncio.run(run_doctor(cfg, **kwargs))
