"""Dataset loading, versioning and stratified sampling."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ProjectConfig, load_entrypoint
from .models import TestCase
from .scorers import Scorer, build_scorer, check_metric_collisions


class DatasetError(ValueError):
    pass


def load_jsonl(path: Path) -> tuple[list[TestCase], str]:
    """Load a JSONL dataset; the version is a hash of its normalized content."""
    if not path.is_file():
        raise DatasetError(f"dataset not found: {path}")
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    cases: list[TestCase] = []
    seen: set[str] = set()
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            case = TestCase.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise DatasetError(f"{path}:{lineno}: {exc}") from exc
        if case.id in seen:
            raise DatasetError(f"{path}:{lineno}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases, hashlib.sha256(raw).hexdigest()[:12]


def write_jsonl(path: Path, cases: list[TestCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False) for c in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def version_of(cases: list[TestCase]) -> str:
    blob = "\n".join(c.content_hash() for c in cases)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def sample_cases(cases: list[TestCase], n: int | None, seed: int = 0) -> list[TestCase]:
    """Deterministic sample stratified by first tag (proportional allocation)."""
    if n is None or n >= len(cases):
        return list(cases)
    strata: dict[str, list[TestCase]] = defaultdict(list)
    for case in cases:
        strata[case.tags[0] if case.tags else ""].append(case)

    def rank(c: TestCase) -> str:
        return hashlib.sha256(f"{seed}:{c.id}".encode()).hexdigest()

    chosen: list[TestCase] = []
    remaining = n
    groups = sorted(strata.items(), key=lambda kv: -len(kv[1]))
    for i, (_, group) in enumerate(groups):
        share = remaining if i == len(groups) - 1 else max(1, round(n * len(group) / len(cases)))
        share = min(share, len(group), remaining)
        chosen.extend(sorted(group, key=rank)[:share])
        remaining -= share
        if remaining <= 0:
            break
    order = {c.id: i for i, c in enumerate(cases)}
    return sorted(chosen, key=lambda c: order[c.id])


@dataclass
class LoadedSuite:
    name: str
    cases: list[TestCase]
    scorers: list[Scorer]
    dataset: str | None
    dataset_version: str
    params: dict[str, Any] = field(default_factory=dict)


def load_suite(cfg: ProjectConfig, name: str, *, sample: int | None = None, seed: int = 0) -> LoadedSuite:
    suite = cfg.suite(name)
    if suite.dataset:
        cases, version = load_jsonl(cfg.resolve_path(suite.dataset))
    else:
        assert suite.generator is not None
        spec = dict(suite.generator)
        fn = load_entrypoint(spec.pop("entrypoint"))
        cases = list(fn(root=str(cfg.root), **spec))
        version = version_of(cases)
    scorers = [build_scorer(s) for s in suite.scorers]
    check_metric_collisions(name, scorers)
    return LoadedSuite(
        name=name,
        cases=sample_cases(cases, sample, seed),
        scorers=scorers,
        dataset=suite.dataset,
        dataset_version=version,
    )
