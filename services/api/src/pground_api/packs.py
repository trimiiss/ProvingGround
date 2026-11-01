"""Uploaded test folders ("packs"): a provingground.yaml plus its datasets, dropped on the dashboard.

Each pack lives in ``packs_dir/<name>`` and its targets join the fleet as agents. The worker runs a
pack's agents with that pack's own config, so packs never mix with each other or with the demo.

An upload is untrusted input. Without ``allow_code_uploads`` it can't run code (no Python
targets, generators or target sources), it can't point at its own provider endpoints (which would
receive the server's API keys), and its config reads only the environment variables in
``PACK_ENV``. Every file stays inside the pack's own folder.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from provingground.config import CONFIG_FILENAME, ConfigError, ProjectConfig, load_config
from provingground.datasets import load_suite

from .settings import get_settings

TEXT_SUFFIXES = frozenset({".yaml", ".yml", ".jsonl", ".json", ".py", ".md", ".txt", ".csv"})
MAX_FILES = 200
MAX_BYTES = 5_000_000
PACK_ENV = ("DEMO_AGENTS_URL",)
CODE_OFF = (
    "{what} runs Python code, and running uploaded code is turned off on this server. Set "
    "PGROUND_ALLOW_CODE_UPLOADS=true on a machine you control, or test the bot over HTTP instead."
)


class PackError(ValueError):
    """A folder that can't be installed. The message is shown to whoever dropped it."""


@dataclass
class Staged:
    name: str
    path: Path
    config: ProjectConfig


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "pack"


def normalize(files: dict[str, str]) -> dict[str, str]:
    """Clean the relative paths, drop the dropped folder's own name, and skip files a pack never needs."""
    if not files:
        raise PackError("the folder is empty")
    if len(files) > MAX_FILES:
        raise PackError(f"too many files ({len(files)}; the limit is {MAX_FILES})")
    if sum(len(v.encode()) for v in files.values()) > MAX_BYTES:
        raise PackError(f"the folder is larger than {MAX_BYTES // 1_000_000} MB")
    clean: dict[str, str] = {}
    for raw, content in files.items():
        path = PurePosixPath(raw.replace("\\", "/").lstrip("/"))
        if ":" in raw or any(part == ".." for part in path.parts) or not path.parts:
            raise PackError(f"bad file path {raw!r}")
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue  # .provingground/ results, .git, caches
        if path.suffix.lower() in TEXT_SUFFIXES:
            clean[path.as_posix()] = content
    # A dropped folder arrives as "my-folder/...": strip that shared first part.
    while clean and all(len(PurePosixPath(k).parts) > 1 for k in clean):
        if len({PurePosixPath(k).parts[0] for k in clean}) != 1:
            break
        clean = {PurePosixPath(*PurePosixPath(k).parts[1:]).as_posix(): v for k, v in clean.items()}
    if CONFIG_FILENAME not in clean:
        raise PackError(f"no {CONFIG_FILENAME} at the top of the folder")
    return clean


def load(root: Path, *, allow_code: bool) -> ProjectConfig:
    """Load a pack's config and check that every suite loads, without running any of its code."""
    path = root / CONFIG_FILENAME
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = load_config(path, env={k: os.environ[k] for k in PACK_ENV if k in os.environ})
    except (ConfigError, yaml.YAMLError) as exc:
        raise PackError(str(exc).replace(str(root), "").lstrip("\\/")) from exc
    if not isinstance(raw, dict):
        raise PackError(f"{CONFIG_FILENAME} must be a mapping")
    if raw.get("providers"):
        raise PackError("an upload can't define providers: use the server's (sim/..., anthropic/...)")
    if raw.get("target_sources"):
        raise PackError("target_sources isn't supported in uploads: list each bot under targets:")
    if not cfg.targets:
        raise PackError(f"{CONFIG_FILENAME} has no targets: add the bot to test under targets:")
    base = root.resolve()
    for name, target in cfg.targets.items():
        if target.kind == "python" and not allow_code:
            raise PackError(CODE_OFF.format(what=f"target {name!r} (kind: python)"))
        if any("${" in value for value in target.headers.values()):
            raise PackError(f"target {name!r}: headers in an upload can't read environment variables")
        missing = [s for s in target.suites if s not in cfg.suites]
        if missing:
            raise PackError(f"target {name!r} uses suites that aren't defined: {', '.join(missing)}")
        if not (target.suites or cfg.suites):
            raise PackError(f"target {name!r} has no suites to run")
    for name, suite in cfg.suites.items():
        if suite.generator and not allow_code:
            raise PackError(CODE_OFF.format(what=f"suite {name!r} (generator)"))
        if suite.dataset and not cfg.resolve_path(suite.dataset).resolve().is_relative_to(base):
            raise PackError(f"suite {name!r}: the dataset must be inside the folder")
        if suite.dataset and not cfg.resolve_path(suite.dataset).is_file():
            raise PackError(f"suite {name!r}: {suite.dataset} is not in the folder")
        try:
            load_suite(cfg, name)
        except Exception as exc:  # bad JSON lines, unknown scorers: the uploader can fix these
            raise PackError(f"suite {name!r}: {exc}") from exc
    return cfg


def stage(files: dict[str, str], *, allow_code: bool) -> Staged:
    """Write the upload to a staging folder and validate it. ``commit`` makes it live."""
    clean = normalize(files)
    root = get_settings().packs_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".incoming-{uuid.uuid4().hex}"
    try:
        for rel, content in clean.items():
            dest = path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        cfg = load(path, allow_code=allow_code)
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return Staged(name=slug(cfg.project), path=path, config=cfg)


def discard(staged: Staged) -> None:
    shutil.rmtree(staged.path, ignore_errors=True)


def commit(staged: Staged) -> ProjectConfig:
    """Replace any earlier upload of the same pack (re-uploading a fixed bot is the normal loop)."""
    final = get_settings().packs_dir / staged.name
    if final.exists():
        shutil.rmtree(final)
    staged.path.rename(final)
    return pack_config(staged.name)


def remove(name: str) -> bool:
    path = get_settings().packs_dir / slug(name)
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


def names() -> list[str]:
    root = get_settings().packs_dir
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def pack_config(name: str) -> ProjectConfig:
    settings = get_settings()
    path = settings.packs_dir / slug(name)
    if not (path / CONFIG_FILENAME).is_file():
        raise PackError(f"uploaded folder {name!r} not found")
    return load(path, allow_code=settings.allow_code_uploads)


def files(name: str) -> list[str]:
    root = get_settings().packs_dir / slug(name)
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


@contextmanager
def isolated_imports(root: Path) -> Iterator[None]:
    """Run a pack's own modules in isolation: two packs may each have a bot.py."""
    saved = list(sys.path)
    before = set(sys.modules)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = saved
        base = root.resolve()
        for module in set(sys.modules) - before:
            file: Any = getattr(sys.modules[module], "__file__", None)
            if file and Path(file).resolve().is_relative_to(base):
                del sys.modules[module]
