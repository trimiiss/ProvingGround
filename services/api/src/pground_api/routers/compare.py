"""Run comparison and quality-gate evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from provingground.compare import Comparison, compare_runs, leaderboard
from provingground.config import GatePolicy
from provingground.gate import GateResult, evaluate_gate

from .. import store
from ..db import models as db
from ..deps import Admin, Session, project_config
from ..schemas import GateBrief, GateEvaluateIn, GateEvaluationOut

router = APIRouter(prefix="/v1", tags=["compare & gates"], dependencies=[Admin])


def gate_brief(g: db.GateEvaluation) -> GateBrief:
    return GateBrief(
        id=g.id,
        policy=g.policy,
        agent=g.agent,
        verdict=g.verdict,
        candidate_run_id=g.candidate_run_id,
        baseline_run_id=g.baseline_run_id,
        created_at=g.created_at,
    )


async def policy_for(session: Session, name: str) -> GatePolicy:
    row = (
        await session.execute(select(db.GatePolicyRow).where(db.GatePolicyRow.name == name))
    ).scalar_one_or_none()
    if row is not None:
        return GatePolicy.model_validate(row.policy)
    cfg = project_config()
    if name in cfg.gates:
        return cfg.gates[name]
    raise HTTPException(404, f"gate policy {name!r} not found")


@router.get("/compare", response_model=Comparison)
async def compare(
    session: Session,
    baseline: Annotated[str, Query(description="baseline run id")],
    candidate: Annotated[str, Query(description="candidate run id")],
    confidence: Annotated[float, Query(gt=0.5, lt=1)] = 0.95,
) -> Comparison:
    base = await store.load_run(session, baseline)
    cand = await store.load_run(session, candidate)
    if base is None or cand is None:
        raise HTTPException(404, "run not found")
    comparison = compare_runs(
        base, cand, confidence=confidence, baseline_label=baseline, candidate_label=candidate
    )
    stmt = select(db.Comparison).where(
        db.Comparison.baseline_run_id == baseline, db.Comparison.candidate_run_id == candidate
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    payload = comparison.model_dump(mode="json")
    if existing is None:
        session.add(
            db.Comparison(
                baseline_run_id=baseline, candidate_run_id=candidate, agent=cand.target.name, payload=payload
            )
        )
    else:
        existing.payload = payload
    return comparison


@router.get("/gates/policies")
async def list_policies(session: Session) -> dict[str, GatePolicy]:
    rows = (await session.execute(select(db.GatePolicyRow))).scalars().all()
    return {r.name: GatePolicy.model_validate(r.policy) for r in rows}


@router.put("/gates/policies/{name}")
async def put_policy(name: str, policy: GatePolicy, session: Session) -> GatePolicy:
    row = (
        await session.execute(select(db.GatePolicyRow).where(db.GatePolicyRow.name == name))
    ).scalar_one_or_none()
    data = policy.model_dump(mode="json")
    if row is None:
        session.add(db.GatePolicyRow(name=name, description=policy.description, policy=data))
    else:
        row.policy, row.description = data, policy.description
    return policy


@router.post("/gates/evaluate", response_model=GateResult)
async def evaluate(
    body: GateEvaluateIn,
    session: Session,
    record: Annotated[bool, Query(description="store the verdict in the gate history")] = True,
) -> GateResult:
    policy = await policy_for(session, body.policy)
    candidate = await store.load_run(session, body.candidate_run_id)
    if candidate is None:
        raise HTTPException(404, f"run {body.candidate_run_id!r} not found")
    comparison: Comparison | None = None
    if body.baseline_run_id:
        baseline = await store.load_run(session, body.baseline_run_id)
        if baseline is None:
            raise HTTPException(404, f"run {body.baseline_run_id!r} not found")
        comparison = compare_runs(baseline, candidate, confidence=policy.confidence)
    result = evaluate_gate(policy, body.policy, candidate, comparison)
    if record:
        await record_gate(session, result)
    return result


async def record_gate(
    session: Session, result: GateResult, when: datetime | None = None
) -> db.GateEvaluation:
    row = db.GateEvaluation(
        policy=result.policy,
        agent=result.target,
        candidate_run_id=result.candidate_id,
        baseline_run_id=result.baseline_id,
        verdict=str(result.verdict),
        exit_code=result.exit_code,
        result=result.model_dump(mode="json"),
    )
    if when is not None:
        row.created_at = when
    session.add(row)
    await session.flush()
    return row


@router.get("/gates/evaluations", response_model=list[GateEvaluationOut])
async def list_evaluations(
    session: Session, agent: str | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 30
) -> list[GateEvaluationOut]:
    stmt = select(db.GateEvaluation)
    if agent:
        stmt = stmt.where(db.GateEvaluation.agent == agent)
    rows = (
        (await session.execute(stmt.order_by(db.GateEvaluation.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [GateEvaluationOut(**gate_brief(g).model_dump(), result=g.result) for g in rows]


@router.get("/gates/evaluations/{evaluation_id}", response_model=GateEvaluationOut)
async def get_evaluation(evaluation_id: int, session: Session) -> GateEvaluationOut:
    g = await session.get(db.GateEvaluation, evaluation_id)
    if g is None:
        raise HTTPException(404, "evaluation not found")
    return GateEvaluationOut(**gate_brief(g).model_dump(), result=g.result)


@router.get("/leaderboard")
async def run_leaderboard(
    session: Session, runs: Annotated[str, Query(description="comma-separated run ids")]
):
    loaded = [await store.load_run(session, rid.strip()) for rid in runs.split(",") if rid.strip()]
    if any(r is None for r in loaded):
        raise HTTPException(404, "run not found")
    return leaderboard([r for r in loaded if r is not None])
