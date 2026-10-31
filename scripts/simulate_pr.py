"""Simulate the pull-request quality gate locally, end to end.

Copies the project into a throwaway git repo, commits it as ``main``, then for each scenario
branches off, edits ``agents.yaml`` the way a developer would (flipping an agent's model),
commits, and runs the *same command the CI workflow runs* (`pground gate-changed`). It asserts
the exit code, so this doubles as an integration test of the full CI path:

    A -> B   must be BLOCKED   (exit 1)
    A -> C   must PASS         (exit 0)
    no-op    must be skipped   (exit 0, nothing evaluated)

Usage:  uv run python scripts/simulate_pr.py [--keep]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPY = ["provingground.yaml", "datasets", "services/demo_agents/agents.yaml"]
AGENT = "support-bot"


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def git(repo: Path, *args: str) -> None:
    run(["git", "-c", "user.name=PR Bot", "-c", "user.email=bot@example.com", *args], repo)


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="pground-sim-pr-"))
    for rel in COPY:
        src, dst = ROOT / rel, repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    run(["git", "init", "-b", "main"], repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "baseline: support-bot on model-a")
    return repo


def flip_model(repo: Path, new_model: str) -> None:
    path = repo / "services/demo_agents/agents.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    in_agent = False
    for i, line in enumerate(lines):
        if line.strip() == f"- slug: {AGENT}":
            in_agent = True
        elif in_agent and line.strip().startswith("- slug:"):
            break
        elif in_agent and line.strip().startswith("model:"):
            lines[i] = line.split("model:")[0] + f"model: {new_model}"
            break
    path.write_text("\n".join(lines), encoding="utf-8")


def scenario(repo: Path, name: str, new_model: str | None, expect_exit: int) -> bool:
    branch = f"pr/{name}"
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", branch)
    if new_model:
        flip_model(repo, new_model)
        git(repo, "commit", "-am", f"switch {AGENT} to {new_model}")
    else:
        (repo / "NOTES.md").write_text("docs only\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "docs: unrelated change")
    out_dir = repo / f"gate-out-{name}"
    result = run(
        [
            sys.executable,
            "-m",
            "provingground.cli",
            "gate-changed",
            "--base",
            "main",
            "--out-dir",
            str(out_dir),
        ],
        repo,
        check=False,
    )
    ok = result.returncode == expect_exit
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name:<14} exit={result.returncode} (expected {expect_exit})")
    if not ok:
        print(result.stdout[-1500:], result.stderr[-1500:])
    report = out_dir / "report.md"
    if report.exists():
        first = next(
            (ln for ln in report.read_text(encoding="utf-8").splitlines() if ln.startswith("##")), ""
        )
        print(f"        {first}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the temporary repo for inspection")
    args = parser.parse_args()
    repo = make_repo()
    print(f"throwaway repo: {repo}\n")
    try:
        results = [
            scenario(repo, "model-b", "sim/model-b", expect_exit=1),
            scenario(repo, "model-c", "sim/model-c", expect_exit=0),
            scenario(repo, "docs-only", None, expect_exit=0),
        ]
    finally:
        if not args.keep:
            shutil.rmtree(repo, ignore_errors=True)
    print("\nAll scenarios behaved as expected." if all(results) else "\nSome scenarios FAILED.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
