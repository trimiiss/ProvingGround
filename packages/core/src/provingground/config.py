"""Project configuration (``provingground.yaml``)."""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .models import ToolPolicy

CONFIG_FILENAME = "provingground.yaml"
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


class ProviderEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["openai_compat", "anthropic"] = "openai_compat"
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "sim/judge"
    effort: str | None = "medium"
    max_tokens: int | None = None
    fallbacks: bool = True  # judge only; targets under test never use server-side fallbacks
    cache: bool = True
    concurrency: int = 8


class ScorerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        if isinstance(value, dict) and "name" not in value and len(value) == 1:
            ((name, params),) = value.items()
            return {"name": name, "params": params or {}}
        if isinstance(value, dict) and "name" in value and "params" not in value:
            rest = {k: v for k, v in value.items() if k != "name"}
            return {"name": value["name"], "params": rest}
        return value


class SuiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    dataset: str | None = None
    generator: dict[str, Any] | None = None  # e.g. the red-team generator (Phase 3)
    scorers: list[ScorerSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _source(self) -> SuiteConfig:
        if not self.dataset and not self.generator:
            raise ValueError("a suite needs a 'dataset' or a 'generator'")
        return self


class TargetConfig(BaseModel):
    """How to build a target. Fields are kind-specific; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["model", "python", "http"]
    description: str = ""
    suites: list[str] = Field(default_factory=list)
    # descriptive metadata shown in the dashboard
    fleet: bool = True  # False: a test fixture / alias, not a monitored agent
    display_name: str | None = None
    team: str | None = None
    runtime: str | None = None
    # kind: model
    model: str | None = None
    system_prompt: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    effort: str | None = None
    # kind: python
    entrypoint: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    # kind: http
    url: str | None = None
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    request: Any = None
    response: dict[str, str] = Field(default_factory=dict)
    timeout_s: float | None = None
    # safety metadata (python targets usually report their own)
    canaries: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    tool_policies: list[ToolPolicy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required(self) -> TargetConfig:
        if self.kind == "model" and not self.model:
            raise ValueError("model targets need 'model'")
        if self.kind == "python" and not self.entrypoint:
            raise ValueError("python targets need 'entrypoint' (module:callable)")
        if self.kind == "http" and not self.url:
            raise ValueError("http targets need 'url'")
        return self

    def with_overrides(self, overrides: dict[str, Any]) -> TargetConfig:
        """Apply ``--set key=value`` overrides (fields first, then python factory args)."""
        if not overrides:
            return self
        data = self.model_dump()
        args = dict(data.get("args") or {})
        for key, value in overrides.items():
            if key in data and key not in {"kind", "args"} and self.kind != "python":
                data[key] = value
            else:
                args[key] = value
        data["args"] = args
        return TargetConfig.model_validate(data)


class TargetSource(BaseModel):
    """A callable that discovers targets (e.g. every agent defined in agents.yaml)."""

    model_config = ConfigDict(extra="forbid")
    entrypoint: str
    args: dict[str, Any] = Field(default_factory=dict)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency: int = 8
    timeout_s: float = 60.0
    retries: int = 2
    repeats: int = 1
    seed: int = 0
    backoff_base_s: float = 0.5


class TrafficConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests_per_month: int = 1_000_000


class GateRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    metric: str | None = None
    min: float | None = None
    max: float | None = None
    max_drop: float | str | None = None  # absolute, or "5%" relative to baseline
    max_increase: float | str | None = None
    critical_cases: Literal["must_pass"] | None = None
    severity: Literal["block", "warn"] = "block"
    # A rule on a metric nobody measures is "N/A" - unless required, which makes it fail instead.
    required: bool = False

    @model_validator(mode="after")
    def _shape(self) -> GateRule:
        if self.critical_cases is None and self.metric is None:
            raise ValueError("a gate rule needs 'metric' or 'critical_cases'")
        if self.metric and all(v is None for v in (self.min, self.max, self.max_drop, self.max_increase)):
            raise ValueError(f"rule on {self.metric!r} has no threshold (min/max/max_drop/max_increase)")
        return self

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.critical_cases:
            return "critical cases must pass"
        return str(self.metric)


class GatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    baseline: str | None = None
    on_inconclusive: Literal["fail", "warn", "pass"] = "fail"
    confidence: float = 0.95
    rules: list[GateRule] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = "default"
    traffic: TrafficConfig = Field(default_factory=TrafficConfig)
    providers: dict[str, ProviderEndpoint] = Field(default_factory=dict)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    defaults: Defaults = Field(default_factory=Defaults)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    target_sources: list[TargetSource] = Field(default_factory=list)
    suites: dict[str, SuiteConfig] = Field(default_factory=dict)
    gates: dict[str, GatePolicy] = Field(default_factory=dict)
    _root: Path = PrivateAttr(default_factory=Path.cwd)

    @field_validator("suites")
    @classmethod
    def _suite_names(cls, value: dict[str, SuiteConfig]) -> dict[str, SuiteConfig]:
        for name in value:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", name):
                raise ValueError(f"invalid suite name {name!r}")
        return value

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self._root / p)

    def all_targets(self) -> dict[str, TargetConfig]:
        targets: dict[str, TargetConfig] = {}
        for source in self.target_sources:
            fn = load_entrypoint(source.entrypoint)
            discovered = fn(root=str(self._root), **source.args)
            for name, cfg in discovered.items():
                targets[name] = cfg if isinstance(cfg, TargetConfig) else TargetConfig.model_validate(cfg)
        targets.update(self.targets)  # explicit config wins
        return targets

    def target(self, name: str) -> TargetConfig:
        targets = self.all_targets()
        if name not in targets:
            known = ", ".join(sorted(targets)) or "none"
            raise ConfigError(f"unknown target {name!r} (known: {known})")
        return targets[name]

    def suite(self, name: str) -> SuiteConfig:
        if name not in self.suites:
            raise ConfigError(f"unknown suite {name!r} (known: {', '.join(sorted(self.suites))})")
        return self.suites[name]

    def gate(self, name: str) -> GatePolicy:
        if name not in self.gates:
            raise ConfigError(f"unknown gate policy {name!r} (known: {', '.join(sorted(self.gates))})")
        return self.gates[name]


def load_entrypoint(spec: str) -> Any:
    if ":" not in spec:
        raise ConfigError(f"entrypoint {spec!r} must look like 'package.module:callable'")
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def find_config(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | Path | None = None) -> ProjectConfig:
    cfg_path = Path(path) if path else find_config()
    if cfg_path is None:
        cfg = ProjectConfig()
        cfg._root = Path.cwd()
        return cfg
    if cfg_path.is_dir():
        cfg_path = cfg_path / CONFIG_FILENAME
    if not cfg_path.is_file():
        raise ConfigError(f"config file not found: {cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    try:
        cfg = ProjectConfig.model_validate(_interpolate(raw))
    except ValueError as exc:
        raise ConfigError(f"{cfg_path}: {exc}") from exc
    cfg._root = cfg_path.parent.resolve()
    return cfg
