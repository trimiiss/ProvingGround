"""A Python callable as the target (e.g. an in-process agent).

``entrypoint`` names a factory ``module:callable``; modules in the project folder (next to
``provingground.yaml``) are importable. The factory receives ``args`` (and ``root`` if it
accepts it) and returns an object with:

* ``async invoke(case, ctx) -> TargetOutput | dict | str`` (required)
* ``describe() -> dict`` (optional): model, description, canaries, forbidden_terms,
  tool_policies, public_info, fingerprint_data, cacheable
"""

from __future__ import annotations

import hashlib
import inspect
import sys
from pathlib import Path
from typing import Any

from ..config import TargetConfig, load_entrypoint
from ..models import TargetInfo, TargetOutput, TestCase, ToolPolicy
from .base import InvokeContext, Target


def code_hash(factory: Any) -> str | None:
    """Hash the factory's module and the .py files beside it: editing bot.py must change the
    fingerprint, or the response cache would replay the old code's answers."""
    try:
        source = inspect.getsourcefile(factory)
    except TypeError:
        return None
    if not source:
        return None
    digest = hashlib.sha256()
    for file in sorted(Path(source).parent.glob("*.py")):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


class PythonTarget(Target):
    def __init__(self, name: str, config: TargetConfig, root: Path | None = None) -> None:
        super().__init__(name, config)
        assert config.entrypoint
        # Appended, not prepended: a project's bot.py is found, but never shadows an installed package.
        if root is not None and str(root) not in sys.path:
            sys.path.append(str(root))
        factory = load_entrypoint(config.entrypoint)
        self._code = code_hash(factory)
        kwargs = dict(config.args)
        if root is not None and "root" in inspect.signature(factory).parameters:
            kwargs.setdefault("root", str(root))
        self.impl = factory(**kwargs)
        self._described: dict[str, Any] = self.impl.describe() if hasattr(self.impl, "describe") else {}

    @property
    def info(self) -> TargetInfo:
        d = self._described
        return TargetInfo(
            name=self.name,
            kind="python",
            model=d.get("model") or self.config.model,
            fingerprint=self._fingerprint({"data": d.get("fingerprint_data"), "code": self._code}),
            description=d.get("description") or self.config.description,
            canaries=[*self.config.canaries, *d.get("canaries", [])],
            forbidden_terms=[*self.config.forbidden_terms, *d.get("forbidden_terms", [])],
            tool_policies=[
                *self.config.tool_policies,
                *(ToolPolicy.model_validate(p) for p in d.get("tool_policies", [])),
            ],
            config={
                "entrypoint": self.config.entrypoint,
                "args": self.config.args,
                "public_info": [*self.config.public_info, *d.get("public_info", [])],
            },
            cacheable=bool(d.get("cacheable", True)),
        )

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        result = await self.impl.invoke(case, ctx)
        if isinstance(result, TargetOutput):
            return result
        if isinstance(result, str):
            return TargetOutput(text=result)
        return TargetOutput.model_validate(result)

    async def aclose(self) -> None:
        close = getattr(self.impl, "aclose", None)
        if close is not None:
            await close()
