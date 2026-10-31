"""Content-addressed JSON caches for target responses and judge verdicts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import TargetOutput


class JsonCache:
    def __init__(self, root: Path | None, namespace: str) -> None:
        self.dir = (root or Path(".provingground/cache")) / namespace

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)  # atomic, so concurrent runs never read half-written entries


class ResponseCache:
    def __init__(self, root: Path | None) -> None:
        self._cache = JsonCache(root, "responses")

    def get(self, key: str) -> TargetOutput | None:
        data = self._cache.get(key)
        return TargetOutput.model_validate(data) if data else None

    def put(self, key: str, output: TargetOutput) -> None:
        self._cache.put(key, output.model_dump(mode="json"))
