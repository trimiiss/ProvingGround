"""Orchestration: turn a config + target + suite names into a finished Run.

Shared by the CLI and the platform worker so both run evaluations identically.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .compare import Comparison, compare_runs
from .config import ProjectConfig, TargetConfig
from .datasets import LoadedSuite, load_suite
from .gate import GateResult, evaluate_gate
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


async def compare_target(
    cfg: ProjectConfig,
    target_name: str,
    baseline_spec: str,
    *,
    suites: list[str] | None = None,
    settings: RunSettings | None = None,
    candidate_overrides: dict[str, object] | None = None,
    baseline_overrides: dict[str, object] | None = None,
    confidence: float = 0.95,
    progress: object = None,
) -> tuple[Comparison, Run, Run]:
    """Evaluate a candidate (with overrides) and a baseline, then compare them per case."""
    from .baseline import resolve_baseline

    settings = settings or settings_from_config(cfg)
    target_cfg = cfg.target(target_name).with_overrides(candidate_overrides or {})
    suite_names = resolve_suites(cfg, target_cfg, suites)
    candidate = await run_target(
        cfg, target_name, suites=suite_names, settings=settings, overrides=candidate_overrides,
        labels={"role": "candidate"}, progress=progress,
    )  # fmt: skip
    if baseline_overrides is not None:
        baseline = await run_target(
            cfg, target_name, suites=suite_names, settings=settings, overrides=baseline_overrides,
            labels={"role": "baseline"},
        )  # fmt: skip
    else:
        baseline = await resolve_baseline(
            baseline_spec,
            cfg,
            target_name,
            suites=suite_names,
            settings=settings,
            labels={"role": "baseline"},
        )
    comparison = compare_runs(
        baseline, candidate, confidence=confidence, seed=settings.seed,
        baseline_label=baseline_spec, candidate_label="candidate",
    )  # fmt: skip
    return comparison, baseline, candidate


async def gate_target(
    cfg: ProjectConfig,
    target_name: str,
    policy_name: str,
    *,
    baseline_override: str | None = None,
    suites: list[str] | None = None,
    settings: RunSettings | None = None,
    candidate_overrides: dict[str, object] | None = None,
    baseline_overrides: dict[str, object] | None = None,
    progress: object = None,
) -> tuple[GateResult, Comparison | None, Run, Run | None]:
    policy = cfg.gate(policy_name)
    # "none" means: no baseline (e.g. a brand-new agent), so only absolute rules apply.
    baseline_spec = None if baseline_override == "none" else (baseline_override or policy.baseline)
    if not baseline_spec and baseline_overrides is None:
        # No baseline: evaluate absolute rules against the candidate alone.
        settings = settings or settings_from_config(cfg)
        candidate = await run_target(cfg, target_name, suites=suites, settings=settings,
                                     overrides=candidate_overrides, progress=progress)  # fmt: skip
        return evaluate_gate(policy, policy_name, candidate, None), None, candidate, None
    comparison, baseline, candidate = await compare_target(
        cfg, target_name, baseline_spec or "", suites=suites, settings=settings,
        candidate_overrides=candidate_overrides, baseline_overrides=baseline_overrides,
        confidence=policy.confidence, progress=progress,
    )  # fmt: skip
    result = evaluate_gate(policy, policy_name, candidate, comparison)
    return result, comparison, candidate, baseline
