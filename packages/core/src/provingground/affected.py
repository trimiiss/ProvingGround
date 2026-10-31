"""Diff-aware gating: which targets differ between a git ref and the working tree?

Each target has a fingerprint (model, prompts, params, knowledge-base version, ...). Comparing
fingerprints at the base ref and in the working tree tells CI exactly which agents a pull
request changed, so a PR that edits one agent only pays to evaluate that agent.

Limitation: the fingerprint covers configuration (agents.yaml, prompts, KB). Changes to
Python *code* are not visible in the base worktree because packages are installed once; treat
code changes as affecting every target (``--all``) in CI if that matters for your repo.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .baseline import BaselineError, _git
from .config import ProjectConfig, load_config
from .engine import build_one_target


@dataclass
class Affected:
    changed: list[str]
    added: list[str]
    removed: list[str]
    unchanged: list[str]


def fingerprints(cfg: ProjectConfig) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in cfg.all_targets():
        target = build_one_target(cfg, name)
        out[name] = target.info.fingerprint
    return out


def affected_targets(cfg: ProjectConfig, base_ref: str) -> Affected:
    root = cfg.root
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref], cwd=root, capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise BaselineError(f"git ref {base_ref!r} not found; fetch it first (e.g. fetch-depth: 0 in CI)")
    worktree = Path(tempfile.mkdtemp(prefix="pground-affected-"))
    try:
        _git(["worktree", "add", "--detach", str(worktree), base_ref], cwd=root)
        base_cfg = load_config(worktree / "provingground.yaml")
        before = fingerprints(base_cfg)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, capture_output=True)
        shutil.rmtree(worktree, ignore_errors=True)
    after = fingerprints(cfg)
    return Affected(
        changed=sorted(n for n in after if n in before and after[n] != before[n]),
        added=sorted(set(after) - set(before)),
        removed=sorted(set(before) - set(after)),
        unchanged=sorted(n for n in after if n in before and after[n] == before[n]),
    )
