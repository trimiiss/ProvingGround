"""Regenerate every demo dataset into datasets/*.jsonl (deterministic).

Run after changing company facts or generators:  uv run python scripts/generate_datasets.py
"""

from __future__ import annotations

from pathlib import Path

from demo_agents import datasets_gen as g
from provingground.datasets import write_jsonl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "datasets"

SUITES = {
    "customer-quality": g.customer_quality,
    "customer-factuality": g.customer_factuality,
    "hr-quality": g.hr_quality,
    "it-quality": g.it_quality,
    "over-refusal": g.over_refusal,
    "security-rag": g.security_rag,
    "security-tools": g.security_tools,
    "tools-refunds": g.tools_refunds,
    "tools-status": g.tools_status,
    "tools-notifications": g.tools_notifications,
    "extraction-invoices": g.extraction_invoices,
    "extraction-receipts": g.extraction_receipts,
    "extraction-leads": g.extraction_leads,
    "classification-tickets": g.classification_tickets,
    "summarization-tickets": g.summarization_tickets,
    "summarization-reviews": g.summarization_reviews,
    "summarization-meetings": g.summarization_meetings,
    "summarization-policies": g.summarization_policies,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, fn in SUITES.items():
        cases = fn()
        write_jsonl(OUT / f"{name}.jsonl", cases)
        total += len(cases)
        print(f"  {name:28s} {len(cases):4d} cases")
    print(f"\nWrote {total} cases across {len(SUITES)} suites to {OUT}")


if __name__ == "__main__":
    main()
