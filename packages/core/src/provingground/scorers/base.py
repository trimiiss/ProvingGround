"""Scorer interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar

from ..config import ScorerSpec
from ..models import Score, TargetInfo, TargetOutput, TestCase

if TYPE_CHECKING:
    from .judge import Judge


@dataclass
class ScoreContext:
    target: TargetInfo
    judge: Judge
    suite: str
    extra: dict[str, Any] = field(default_factory=dict)


class Scorer(ABC):
    name: ClassVar[str]
    version: ClassVar[str] = "1"
    #: Metrics this scorer can emit (used to reject suites where two scorers collide).
    metrics: ClassVar[tuple[str, ...]] = ()
    #: Case fields the scorer needs: "expected", "context".
    requires: ClassVar[frozenset[str]] = frozenset()
    uses_judge: ClassVar[bool] = False

    def __init__(self, **params: Any) -> None:
        self.params = params

    def applies(self, case: TestCase, output: TargetOutput) -> bool:
        if "expected" in self.requires and case.expected is None:
            return False
        return not ("context" in self.requires and not (case.context or output.retrieved_context))

    @abstractmethod
    async def score(self, case: TestCase, output: TargetOutput, ctx: ScoreContext) -> list[Score]: ...

    def make(
        self, metric: str, value: float | None, passed: bool | None = None, reason: str = "", **details: Any
    ) -> Score:
        return Score(
            metric=metric, value=value, passed=passed, reason=reason, details=details,
            scorer=self.name, scorer_version=self.version,
        )  # fmt: skip

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


REGISTRY: dict[str, type[Scorer]] = {}


def register[S: type[Scorer]](cls: S) -> S:
    REGISTRY[cls.name] = cls
    return cls


def _plugin(name: str) -> type[Scorer] | None:
    for ep in entry_points(group="provingground.scorers"):
        if ep.name == name:
            loaded = ep.load()
            if isinstance(loaded, type) and issubclass(loaded, Scorer):
                return loaded
    return None


def build_scorer(spec: ScorerSpec | str | dict[str, object]) -> Scorer:
    spec = spec if isinstance(spec, ScorerSpec) else ScorerSpec.model_validate(spec)
    cls = REGISTRY.get(spec.name) or _plugin(spec.name)
    if cls is None:
        raise ValueError(f"unknown scorer {spec.name!r} (known: {', '.join(sorted(REGISTRY))})")
    return cls(**spec.params)


def check_metric_collisions(suite: str, scorers: list[Scorer]) -> None:
    owners: dict[str, str] = {}
    for scorer in scorers:
        for metric in scorer.metrics:
            if metric in owners and owners[metric] != scorer.name:
                raise ValueError(
                    f"suite {suite!r}: metric {metric!r} is emitted by both {owners[metric]!r} and {scorer.name!r}"
                )
            owners[metric] = scorer.name
