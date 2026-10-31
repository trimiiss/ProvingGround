"""Entry points used by ``provingground.yaml``: build one agent as a python target, or discover all."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from provingground.models import TargetOutput, TestCase
from provingground.targets import InvokeContext

from .runtime import Agent, AgentRequest, AgentSpec

DEFAULT_AGENTS_FILE = "services/demo_agents/agents.yaml"


def _resolve(agents_file: str | None, root: str | None) -> Path:
    path = Path(agents_file or DEFAULT_AGENTS_FILE)
    if not path.is_absolute():
        path = Path(root or ".") / path
    return path


def load_specs(agents_file: str | None = None, root: str | None = None) -> dict[str, AgentSpec]:
    path = _resolve(agents_file, root)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs = [AgentSpec.model_validate(a) for a in data.get("agents", [])]
    return {s.slug: s for s in specs}


class AgentTarget:
    """Adapter from an Agent to the PythonTarget protocol (invoke + describe)."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    def describe(self) -> dict[str, Any]:
        return self.agent.describe()

    async def invoke(self, case: TestCase, ctx: InvokeContext) -> TargetOutput:
        req = AgentRequest(input=case.input, messages=case.messages, context=case.context, case_id=case.id)
        return await self.agent.run(req)


def build_agent(
    agent: str, model: str | None = None, agents_file: str | None = None, root: str | None = None
) -> AgentTarget:
    specs = load_specs(agents_file, root)
    if agent not in specs:
        raise KeyError(f"unknown agent {agent!r} in {_resolve(agents_file, root)}")
    spec = specs[agent]
    if model:
        spec = spec.model_copy(update={"model": model})
    return AgentTarget(Agent(spec))


def discover_targets(root: str | None = None, agents_file: str | None = None) -> dict[str, dict[str, Any]]:
    """One python target per agent in agents.yaml, carrying the agent's suites."""
    return {
        slug: {
            "kind": "python",
            "entrypoint": "demo_agents.entry:build_agent",
            "args": {"agent": slug, "agents_file": agents_file or DEFAULT_AGENTS_FILE},
            "suites": spec.suites,
            "description": spec.description,
        }
        for slug, spec in load_specs(agents_file, root).items()
    }
