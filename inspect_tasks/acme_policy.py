"""An Inspect AI task over Acme's policy facts - runs against any ProvingGround target.

uv run pground inspect --target support-bot --task inspect_tasks/acme_policy.py
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate

from demo_agents.company import CUSTOMER_FACTS


@task
def acme_policy() -> Task:
    """Does the assistant state the key number from each policy? (e.g. '30 days')."""
    import re

    samples = []
    for fact in CUSTOMER_FACTS:
        match = re.search(
            r"(\d[\d,.]*\s*(?:%|days?|hours?|weeks?|years?|business days)|\$[\d,.]+)", fact.answer
        )
        if not match:
            continue
        samples.append(Sample(input=fact.questions[0], target=match.group(1).split()[0], id=fact.id))
    return Task(dataset=MemoryDataset(samples), solver=[generate()], scorer=includes())
