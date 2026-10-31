"""Target interface: the AI system under test."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..config import TargetConfig
from ..models import TargetInfo, TargetOutput, TestCase, sha256_hex


class TargetError(Exception):
    """Raised by targets; ``transient`` errors are retried by the runner."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class InvokeContext:
    run_id: str
    suite: str
    repeat: int = 0
    timeout_s: float = 60.0
    seed: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


SECRET_HEADER_HINTS = ("authorization", "api-key", "x-api-key", "token", "secret", "password")


def sanitized(config: TargetConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json", exclude={"description", "suites"}, exclude_defaults=True)
    headers = data.get("headers")
    if isinstance(headers, dict):
        data["headers"] = {
            k: ("***" if any(h in k.lower() for h in SECRET_HEADER_HINTS) else v) for k, v in headers.items()
        }
    return data


class Target(ABC):
    def __init__(self, name: str, config: TargetConfig) -> None:
        self.name = name
        self.config = config

    @property
    @abstractmethod
    def info(self) -> TargetInfo: ...

    @abstractmethod
    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass

    def _fingerprint(self, extra: Any = None) -> str:
        return sha256_hex({"config": sanitized(self.config), "extra": extra, "engine": __version__})[:16]
