"""Local results store: ``.provingground/runs/<run-id>/run.json`` plus rendered reports."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from .models import GitInfo, Run

HOME_DIRNAME = ".provingground"


class RunIndexEntry(BaseModel):
    id: str
    created_at: datetime
    target: str
    model: str | None
    n_results: int
    pass_rate: float | None
    labels: dict[str, str] = {}


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root / HOME_DIRNAME / "runs"

    def dir_for(self, run_id: str) -> Path:
        return self.root / run_id

    def save(self, run: Run, reports: dict[str, str] | None = None) -> Path:
        directory = self.dir_for(run.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run.json").write_text(run.model_dump_json(indent=1), encoding="utf-8")
        for name, content in (reports or {}).items():
            (directory / name).write_text(content, encoding="utf-8")
        return directory

    def load(self, ref: str) -> Run:
        path = Path(ref)
        if path.is_dir():
            path = path / "run.json"
        if not path.is_file():
            if ref == "latest":
                entries = self.list()
                if not entries:
                    raise FileNotFoundError("no runs stored yet")
                path = self.dir_for(entries[0].id) / "run.json"
            else:
                path = self.dir_for(ref) / "run.json"
        if not path.is_file():
            raise FileNotFoundError(f"run not found: {ref}")
        return Run.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, target: str | None = None) -> list[RunIndexEntry]:
        entries: list[RunIndexEntry] = []
        if not self.root.is_dir():
            return entries
        for run_file in self.root.glob("*/run.json"):
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entry = RunIndexEntry(
                id=data["id"],
                created_at=data["created_at"],
                target=data["target"]["name"],
                model=data["target"].get("model"),
                n_results=data["summary"].get("n_results", 0),
                pass_rate=data["summary"].get("pass_rate"),
                labels=data.get("labels", {}),
            )
            if target is None or entry.target == target:
                entries.append(entry)
        return sorted(entries, key=lambda e: e.created_at, reverse=True)


def current_git(cwd: Path | None = None) -> GitInfo | None:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    sha = git("rev-parse", "HEAD")
    if sha is None:
        return None
    status = git("status", "--porcelain")
    return GitInfo(sha=sha, ref=git("rev-parse", "--abbrev-ref", "HEAD"), dirty=bool(status))
