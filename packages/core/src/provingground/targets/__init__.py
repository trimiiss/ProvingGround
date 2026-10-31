"""Target construction."""

from __future__ import annotations

from pathlib import Path

from ..config import TargetConfig
from ..providers import ProviderRegistry
from .base import InvokeContext, Target, TargetError
from .http import HttpTarget
from .model import ModelTarget, format_context
from .python import PythonTarget


def build_target(
    name: str, config: TargetConfig, *, root: Path | None = None, registry: ProviderRegistry | None = None
) -> Target:
    if config.kind == "model":
        return ModelTarget(name, config, registry=registry)
    if config.kind == "python":
        return PythonTarget(name, config, root=root)
    if config.kind == "http":
        return HttpTarget(name, config)
    raise ValueError(f"unknown target kind {config.kind!r}")


__all__ = [
    "HttpTarget",
    "InvokeContext",
    "ModelTarget",
    "PythonTarget",
    "Target",
    "TargetError",
    "build_target",
    "format_context",
]
