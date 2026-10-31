"""A Python callable as the target (e.g. an in-process agent).

``entrypoint`` names a factory ``module:callable``. The factory receives ``args`` (and
``root`` if it accepts it) and returns an object with:

* ``async invoke(case, ctx) -> TargetOutput | dict | str`` (required)
* ``describe() -> dict`` (optional): model, description, canaries, forbidden_terms,
  tool_policies, public_info, fingerprint_data, cacheable
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ..config import TargetConfig, load_entrypoint
from ..models import TargetInfo, TargetOutput, TestCase, ToolPolicy
from .base import InvokeContext, Target


class PythonTarget(Target):
    def __init__(self, name: str, config: TargetConfig, root: Path | None = None) -> None:
        super().__init__(name, config)
        assert config.entrypoint
        factory = load_entrypoint(config.entrypoint)
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
            fingerprint=self._fingerprint(d.get("fingerprint_data")),
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
                "public_info": d.get("public_info", []),
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
