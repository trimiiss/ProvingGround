"""The full CI path in a throwaway git repo: changed-agent detection, rerun baseline, exit codes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    spec = importlib.util.spec_from_file_location("simulate_pr", ROOT / "scripts" / "simulate_pr.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pr_scenarios_behave_as_expected() -> None:
    sim = _load_script()
    repo = sim.make_repo()
    try:
        assert sim.scenario(repo, "model-b", "sim/model-b", expect_exit=1)  # regression is blocked
        assert sim.scenario(repo, "model-c", "sim/model-c", expect_exit=0)  # safe swap passes
        assert sim.scenario(repo, "docs-only", None, expect_exit=0)  # nothing to evaluate
    finally:
        import shutil

        shutil.rmtree(repo, ignore_errors=True)
