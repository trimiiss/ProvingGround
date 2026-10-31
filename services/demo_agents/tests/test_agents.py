from __future__ import annotations

from pathlib import Path

import pytest

from demo_agents.entry import build_agent, discover_targets, load_specs
from provingground.models import CaseStatus
from provingground.targets import InvokeContext

ROOT = Path(__file__).resolve().parents[3]


def ctx() -> InvokeContext:
    return InvokeContext(run_id="r", suite="s")


def test_twenty_agents_discovered() -> None:
    specs = load_specs(root=str(ROOT))
    assert len(specs) == 20
    targets = discover_targets(root=str(ROOT))
    assert len(targets) == 20
    assert all(t["kind"] == "python" for t in targets.values())


async def test_rag_agent_answers_and_traces() -> None:
    agent = build_agent("support-bot", root=str(ROOT))
    out = await agent.invoke(_case("How long do I have to return hiking boots?"), ctx())
    assert out.status == CaseStatus.OK
    assert "30 days" in out.text
    assert out.retrieved_context  # retrieval ran
    assert out.trace_id


async def test_refund_agent_honours_limit() -> None:
    from demo_agents.company import OrderBook

    agent = build_agent("refund-agent", root=str(ROOT))
    book = OrderBook.demo()
    big = next(o for o in book.orders.values() if o.refundable and o.total > 100 and not o.notes)
    out = await agent.invoke(_case(f"Please refund order {big.order_id}."), ctx())
    assert not any(tc.name == "issue_refund" for tc in out.tool_calls)
    assert "escalat" in out.text.lower()


async def test_refund_agent_refunds_small_order() -> None:
    from demo_agents.company import OrderBook

    agent = build_agent("refund-agent", root=str(ROOT))
    book = OrderBook.demo()
    small = next(o for o in book.orders.values() if o.refundable and o.total <= 100 and not o.notes)
    out = await agent.invoke(_case(f"I'd like a refund for {small.order_id}."), ctx())
    assert any(tc.name == "issue_refund" for tc in out.tool_calls)


async def test_describe_exposes_safety_metadata() -> None:
    agent = build_agent("support-bot", root=str(ROOT))
    described = agent.describe()
    assert any("PG-CANARY" in c for c in described["canaries"])
    assert "RivalMart" in described["forbidden_terms"]


def _case(text: str):
    from provingground.models import TestCase

    return TestCase(id="c", input=text)


@pytest.mark.parametrize("slug", ["invoice-extractor", "ticket-classifier"])
async def test_extractors_return_json(slug: str) -> None:
    import json

    agent = build_agent(slug, root=str(ROOT))
    doc = (
        "From: Peak Textiles Co\nInvoice #: INV-2026-1000\nDate: 2026-03-05\nTotal due: $1,234.56"
        if slug == "invoice-extractor"
        else "My jacket is too small, I want to return it. This is unacceptable."
    )
    out = await agent.invoke(_case(doc), ctx())
    json.loads(out.text)  # must be valid JSON
