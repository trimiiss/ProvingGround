"""Generate live, production-like traffic against the demo agents service.

    uv run python scripts/traffic.py --url http://localhost:8100 --rate 4 --duration 120

Requests are sampled from the demo datasets (weighted so a few agents are busy), with a small
share of adversarial prompts mixed in. The agents service exports its spans to the platform over
OTLP, so traces, Prometheus metrics and the dashboard fill up in real time.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from provingground.config import load_config  # noqa: E402
from provingground.datasets import load_jsonl  # noqa: E402

#: relative request volume per agent
WEIGHTS = {
    "support-bot": 12, "returns-assistant": 5, "shipping-helper": 4, "store-info-bot": 3, "warranty-advisor": 3,
    "loyalty-concierge": 2, "order-status-agent": 4, "refund-agent": 2, "notification-agent": 2, "exchange-agent": 1,
    "hr-policy-qa": 2, "it-helpdesk": 3, "invoice-extractor": 2, "receipt-parser": 1, "lead-qualifier": 1,
    "ticket-classifier": 3, "ticket-summarizer": 2, "review-summarizer": 1, "meeting-notes": 1, "policy-digest": 1,
}  # fmt: skip


def load_pools(cfg):  # type: ignore[no-untyped-def]
    """agent -> (normal cases, attack cases)."""
    pools = {}
    for name, target in cfg.all_targets().items():
        normal, attacks = [], []
        for suite_name in target.suites:
            suite = cfg.suites.get(suite_name)
            if suite is None or not suite.dataset:
                continue
            cases, _ = load_jsonl(cfg.resolve_path(suite.dataset))
            (attacks if suite_name.startswith("security-") else normal).extend(cases)
        pools[name] = (normal, attacks)
    return pools


async def one(client: httpx.AsyncClient, base: str, agent: str, case, stats: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    body = {"input": case.input, "messages": [m.model_dump(mode="json") for m in case.messages] if case.messages else None,
            "context": case.context, "case_id": None}  # fmt: skip
    try:
        resp = await client.post(f"{base}/agents/{agent}/chat", json=body, timeout=60)
        stats["ok" if resp.status_code == 200 else "error"] += 1
    except httpx.HTTPError:
        stats["error"] += 1


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://localhost:8100", help="demo agents service base URL")
    parser.add_argument("--rate", type=float, default=4.0, help="requests per second")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds to run (0 = forever)")
    parser.add_argument(
        "--attack-share", type=float, default=0.04, help="fraction of requests that are adversarial"
    )
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    cfg = load_config(ROOT / "provingground.yaml")
    pools = {a: p for a, p in load_pools(cfg).items() if p[0] and a in WEIGHTS}
    agents = list(pools)
    weights = [WEIGHTS[a] for a in agents]
    stats = {"ok": 0, "error": 0}
    started = time.monotonic()
    pending: set[asyncio.Task[None]] = set()
    async with httpx.AsyncClient() as client:
        while args.duration == 0 or time.monotonic() - started < args.duration:
            agent = rng.choices(agents, weights)[0]
            normal, attacks = pools[agent]
            case = rng.choice(attacks) if attacks and rng.random() < args.attack_share else rng.choice(normal)
            task = asyncio.create_task(one(client, args.url, agent, case, stats))
            pending.add(task)
            task.add_done_callback(pending.discard)
            await asyncio.sleep(rng.expovariate(args.rate))  # Poisson arrivals
        await asyncio.gather(*pending)
    print(
        f"sent {stats['ok'] + stats['error']} requests in {time.monotonic() - started:.0f}s: {stats['ok']} ok, {stats['error']} errors"
    )


if __name__ == "__main__":
    asyncio.run(main())
