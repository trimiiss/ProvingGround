"""Shared plumbing for the DeepEval / Ragas adapters."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import threading
from collections.abc import Coroutine
from typing import Any

from ...models import Usage


class AdapterNotInstalled(RuntimeError):
    pass


def require(package: str, extra: str = "adapters") -> None:
    if importlib.util.find_spec(package) is None:
        raise AdapterNotInstalled(
            f"{package} is not installed. Install the adapters extra: `pip install 'provingground[{extra}]'` "
            "(or `uv sync --extra adapters`)."
        )


class UsageSink:
    """Accumulates judge usage/cost across the many LLM calls one framework metric makes."""

    def __init__(self) -> None:
        self.usage = Usage()
        self.cost = 0.0
        self.cost_known = True
        self.model: str | None = None
        self.calls = 0
        self._lock = threading.Lock()

    def add(self, usage: Usage, cost: float | None, model: str) -> None:
        with self._lock:
            self.usage = self.usage + usage
            self.model = model
            self.calls += 1
            if cost is None:
                self.cost_known = False
            else:
                self.cost += cost

    @property
    def total_cost(self) -> float | None:
        return self.cost if self.cost_known and self.calls else None


def extract_json(text: str) -> Any:
    """Parse a JSON object from a model reply that may include fences or prose."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def run_coroutine_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from sync code, even when an event loop is already running here."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def minimal_instance(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """The smallest valid JSON value for a JSON schema (used by offline stub judges)."""
    defs = defs or schema.get("$defs", {})
    if "$ref" in schema:
        return minimal_instance(defs[schema["$ref"].split("/")[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return minimal_instance(options[0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            k: minimal_instance(v, defs)
            for k, v in schema.get("properties", {}).items()
            if k in schema.get("required", [])
        }
    if kind == "array":
        return []
    if kind == "string":
        return "ok"
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if kind == "boolean":
        return True
    return None
