"""Orchestration: turn a config + target + suite names into a finished Run.

Shared by the CLI and the platform worker so both run evaluations identically.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .config import ProjectConfig, TargetConfig
from .datasets import LoadedSuite, load_suite
from .models import Run
from .providers import ProviderRegistry
from .runner import RunSettings, execute_run
from .scorers import build_judge
from .store import current_git
from .targets import Target, build_target


def resolve_suites(cfg: ProjectConfig, target_cfg: TargetConfig, names: list[str] | None) -> list[str]:
    if names:
        return names
    if target_cfg.suites:
        return list(target_cfg.suites)
    return list(cfg.suites)


def settings_from_config(cfg: ProjectConfig, **overrides: object) -> RunSettings:
    d = cfg.defaults
    base = RunSettings(
        repeats=d.repeats, seed=d.seed, concurrency=d.concurrency, timeout_s=d.timeout_s,
        retries=d.retries, backoff_base_s=d.backoff_base_s, cache_dir=cfg.root / ".provingground" / "cache",
    )  # fmt: skip
    return replace(base, **{k: v for k, v in overrides.items() if v is not None})  # type: ignore[arg-type]


async def run_target(
    cfg: ProjectConfig,
    target_name: str,
    *,
    suites: list[str] | None = None,
    settings: RunSettings | None = None,
    overrides: dict[str, object] | None = None,
    labels: dict[str, str] | None = None,
    registry: ProviderRegistry | None = None,
    progress: object = None,
) -> Run:
    registry = registry or ProviderRegistry(cfg.providers)
    target_cfg = cfg.target(target_name).with_overrides(overrides or {})
    suite_names = resolve_suites(cfg, target_cfg, suites)
    settings = settings or settings_from_config(cfg)
    loaded: list[LoadedSuite] = [
        load_suite(cfg, name, sample=settings.sample, seed=settings.seed) for name in suite_names
    ]
    judge = build_judge(cfg.judge, registry, cache_dir=settings.cache_dir if cfg.judge.cache else None)
    target: Target = build_target(target_name, target_cfg, root=cfg.root, registry=registry)
    try:
        run = await execute_run(
            target, loaded, settings, judge, project=cfg.project, labels=labels,
            git=current_git(cfg.root), progress=progress,  # type: ignore[arg-type]
        )  # fmt: skip
    finally:
        await target.aclose()
    return run


def build_one_target(cfg: ProjectConfig, name: str, overrides: dict[str, object] | None = None) -> Target:
    target_cfg = cfg.target(name).with_overrides(overrides or {})
    return build_target(name, target_cfg, root=cfg.root, registry=ProviderRegistry(cfg.providers))


def reports_dir(cfg: ProjectConfig) -> Path:
    return cfg.root / ".provingground"
