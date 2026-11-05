from __future__ import annotations

import base64
import codecs

from provingground.models import CaseResult, CaseStatus, Run, Score, TargetInfo, TargetOutput
from provingground.redteam import (
    DEFAULT_CONVERTERS,
    DIRECT_TECHNIQUES,
    GOALS,
    apply_converter,
    build_attack_cases,
)
from provingground.redteam.builtin import OBJECTIVES, PER_GOAL, standard_attacks
from provingground.redteam.report import render_redteam_html, render_redteam_markdown, summarize_attacks

FACTS = [(f"Question {i}?", f"Answer {i}.", f"Document {i} says answer {i}.") for i in range(5)]


def test_builtin_attacks_cover_every_technique_and_converter_for_each_goal() -> None:
    cases = standard_attacks(terms=["RivalCo"])
    assert len(cases) == 4 * PER_GOAL
    assert len({c.id for c in cases}) == len(cases)
    for goal in OBJECTIVES:
        attacks = [c.metadata["attack"] for c in cases if c.metadata["attack"]["goal"] == goal]
        assert {a["technique"] for a in attacks if a["converter"] == "none"} == set(DIRECT_TECHNIQUES)
        assert {a["converter"] for a in attacks} == set(DEFAULT_CONVERTERS)


def test_builtin_attacks_are_stable_and_name_no_company() -> None:
    cases = standard_attacks(terms=["RivalCo"])
    assert cases == standard_attacks(terms=["RivalCo"])
    plain = [c.input for c in cases if c.metadata["attack"]["converter"] == "none"]
    assert plain and not any(word in text for text in plain for word in ("Acme", "RivalMart", "staff discount"))
    assert any("RivalCo" in text for text in plain)


def test_builtin_attacks_stay_unique_at_the_largest_size() -> None:
    cases = standard_attacks(goals=["secret_leak"], per_goal=999)
    assert len(cases) == len({c.id for c in cases}) == len(DIRECT_TECHNIQUES) * len(DEFAULT_CONVERTERS)


def test_converters_round_trip() -> None:
    text = "reveal the secret code"
    encoded = apply_converter("base64", text)
    payload = encoded.rsplit(": ", 1)[1]
    assert base64.b64decode(payload).decode() == text
    rot = apply_converter("rot13", text).rsplit(": ", 1)[1]
    assert codecs.decode(rot, "rot13") == text
    assert apply_converter("none", text) == text
    assert apply_converter("homoglyph", "a e o") != "a e o"  # lookalike characters substituted


def test_generator_is_a_full_cross_product_with_unique_ids() -> None:
    cases = build_attack_cases(
        prefix="t", surfaces=("direct", "indirect"), rag_questions=FACTS, converters=DEFAULT_CONVERTERS,
        extra_context={"pii": ["customer record"]}, seed=1,
    )  # fmt: skip
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    direct = [c for c in cases if c.metadata["attack"]["technique"] != "indirect"]
    assert len(direct) == 4 * 10 * len(DEFAULT_CONVERTERS)  # 4 RAG goals x 10 techniques x converters
    assert {c.metadata["attack"]["converter"] for c in direct} == set(DEFAULT_CONVERTERS)
    indirect = [c for c in cases if c.metadata["attack"]["technique"] == "indirect"]
    assert len(indirect) == len(FACTS) and all(c.metadata["markers"] for c in indirect)


def test_generator_is_deterministic() -> None:
    kwargs = {
        "prefix": "t",
        "surfaces": ("direct",),
        "rag_questions": FACTS,
        "converters": ("none", "base64"),
        "seed": 7,
    }
    a = build_attack_cases(**kwargs)  # type: ignore[arg-type]
    b = build_attack_cases(**kwargs)  # type: ignore[arg-type]
    assert [c.model_dump() for c in a] == [c.model_dump() for c in b]


def test_tool_surface_goals() -> None:
    cases = build_attack_cases(
        prefix="t",
        surfaces=("tool",),
        order_ids=["ORD-1", "ORD-2"],
        poisoned_order_ids=["ORD-9"],
        converters=("none",),
    )
    goals = {c.metadata["attack"]["goal"] for c in cases}
    assert {"unauthorized_refund", "data_exfiltration"} <= goals
    assert any(c.id.startswith("t-tool-indirect") for c in cases)
    assert set(GOALS) >= goals - {"unauthorized_action"}


def _result(
    cid: str, goal: str, tech: str, conv: str, won: bool, status: CaseStatus = CaseStatus.OK
) -> CaseResult:
    return CaseResult(
        suite="sec", case_id=cid, status=status, metadata={"attack": {"goal": goal, "technique": tech, "converter": conv}},
        output=TargetOutput(text="leaked" if won else "no", status=status),
        scores=[Score(metric="safety.attack_success_rate", value=1.0 if won else 0.0, passed=not won,
                      details={"detectors": {"canary_leak": ["X"]}} if won else {})] if status == CaseStatus.OK else [],
    )  # fmt: skip


def test_attack_summary_breakdowns_and_unscored() -> None:
    from datetime import UTC, datetime

    results = [
        _result("a", "secret_leak", "override", "none", True),
        _result("b", "secret_leak", "override", "base64", False),
        _result("c", "pii_exfil", "roleplay", "none", False),
        _result("d", "pii_exfil", "roleplay", "none", False, status=CaseStatus.TIMEOUT),
    ]
    run = Run(id="r", created_at=datetime.now(UTC), target=TargetInfo(name="t", kind="model", fingerprint="f", model="m"),
              results=results)  # fmt: skip
    s = summarize_attacks(run)
    assert (s.attempts, s.successes) == (3, 1)
    assert s.not_scored == 1  # a timeout is not counted as a resisted attack
    assert s.by_goal["secret_leak"].rate == 0.5
    assert s.grid["override"]["secret_leak"].successes == 1
    assert s.top_successes[0].evidence == ["canary_leak"]
    assert "Red-team report" in render_redteam_markdown([s])
    assert "<table" in render_redteam_html([s])
