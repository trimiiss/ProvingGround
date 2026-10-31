"""Scorers. Importing this package registers every built-in scorer."""

from __future__ import annotations

from . import adapters, deterministic, judge, ml, selfcheck  # noqa: F401  (registration side effects)
from .base import REGISTRY, ScoreContext, Scorer, build_scorer, check_metric_collisions, register
from .judge import Judge, JudgeCompletion, JudgeError, JudgeVerdict, LLMJudge, SimJudge, build_judge

__all__ = [
    "REGISTRY",
    "Judge",
    "JudgeCompletion",
    "JudgeError",
    "JudgeVerdict",
    "LLMJudge",
    "ScoreContext",
    "Scorer",
    "SimJudge",
    "build_judge",
    "build_scorer",
    "check_metric_collisions",
    "register",
]
