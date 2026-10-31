"""Resolve a gate baseline into a Run.

Strategies (the ``baseline:`` field of a gate policy, or ``--baseline`` on the CLI):

* ``file:<path>``        - load a saved run.json.
* ``run:<id>`` / ``latest`` / a bare run id - load from the local run store.
* ``rerun:<git-ref>``    - check the ref out into a temporary ``git worktree``, evaluate the
                           same target and suites there, and return that run. Reuses the
                           response cache, so a rerun of unchanged agents is nearly free.
* ``api:<agent>@<ref>``  - pull a stored baseline from the platform (Phase 4).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from .config import ProjectConfig, load_config
from .engine import run_target
from .models import Run
from .runner import RunSettings
from .store import RunStore


class BaselineError(RuntimeError):
    pass


async def resolve_baseline(
    spec: str,
    cfg: ProjectConfig,
    target: str,
    *,
    suites: list[str] | None,
    settings: RunSettings,
    labels: dict[str, str] | None = None,
) -> Run:
    kind, _, arg = spec.partition(":")
    if spec == "latest" or (kind == "run" and arg):
        return RunStore(cfg.root).load(arg or "latest")
    if kind == "file":
        path = cfg.resolve_path(arg)
        if not path.exists():
            raise BaselineError(f"baseline file not found: {path}")
        return (
            RunStore(cfg.root).load(str(path))
            if path.name == "run.json" or path.is_dir()
            else _load_json(path)
        )
    if kind == "rerun":
        return await _rerun_baseline(arg, cfg, target, suites=suites, settings=settings, labels=labels)
    if kind == "api":
        raise BaselineError(
            "api: baselines require the ProvingGround platform (Phase 4); use rerun: or file: for now"
        )
    # bare value: treat as a run id
    return RunStore(cfg.root).load(spec)


def _load_json(path: Path) -> Run:
    return Run.model_validate_json(path.read_text(encoding="utf-8"))


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise BaselineError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


async def _rerun_baseline(
    ref: str,
    cfg: ProjectConfig,
    target: str,
    *,
    suites: list[str] | None,
    settings: RunSettings,
    labels: dict[str, str] | None,
) -> Run:
    root = cfg.root
    if not (root / ".git").exists() and not _is_in_git(root):
        raise BaselineError(f"{root} is not a git repository; 'rerun:' needs git. Use file: or run: instead.")
    worktree = Path(tempfile.mkdtemp(prefix="pground-baseline-"))
    try:
        _git(["worktree", "add", "--detach", str(worktree), ref], cwd=root)
        base_cfg = load_config(worktree / "provingground.yaml")
        if target not in base_cfg.all_targets():
            raise BaselineError(f"target {target!r} does not exist at {ref}; nothing to compare against")
        # Reuse the candidate's response cache: unchanged agents hit it, changed agents miss and re-run.
        base_settings = replace(settings, cache_dir=settings.cache_dir)
        return await run_target(
            base_cfg, target, suites=suites, settings=base_settings,
            labels={**(labels or {}), "baseline_ref": ref},
        )  # fmt: skip
    finally:
        _git(["worktree", "remove", "--force", str(worktree)], cwd=root) if worktree.exists() else None
        shutil.rmtree(worktree, ignore_errors=True)


def _is_in_git(path: Path) -> bool:
    try:
        return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path,
                              capture_output=True, text=True, timeout=10).returncode == 0  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
