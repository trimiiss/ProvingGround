"""Upload a test folder from the dashboard: its bots join the fleet and are evaluated right away."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import packs, queue
from ..db import models as db
from ..deps import Admin, Session, project_config
from ..schemas import JobOut
from ..settings import get_settings
from .runs import job_out

router = APIRouter(prefix="/v1", tags=["uploads"], dependencies=[Admin])


class PackIn(BaseModel):
    files: dict[str, str] = Field(description="relative path -> text content, e.g. a dropped folder")


class PackOut(BaseModel):
    name: str
    agents: list[str]
    files: list[str]
    gate: str | None = None


class PackUploaded(PackOut):
    jobs: list[JobOut]


def release_gate(cfg: Any) -> str | None:
    return "release" if "release" in cfg.gates else next(iter(sorted(cfg.gates)), None)


async def pack_agents(session: Session, name: str) -> list[db.Agent]:
    rows = (await session.execute(select(db.Agent).order_by(db.Agent.slug))).scalars()
    return [a for a in rows if (a.config or {}).get("pack") == name]


async def check_names(session: Session, staged: packs.Staged) -> None:
    """A bot's name is its agent id: it can't take over the demo's or another upload's agent."""
    demo = project_config().all_targets()
    agents = {a.slug: a for a in (await session.execute(select(db.Agent))).scalars()}
    for name in staged.config.targets:
        owner = (agents[name].config or {}).get("pack") if name in agents else None
        if name in demo:
            raise packs.PackError(
                f"an agent named {name!r} already exists in the demo: rename it under targets:"
            )
        if owner and owner != staged.name:
            raise packs.PackError(
                f"an agent named {name!r} already comes from the upload {owner!r}: rename it"
            )


async def register(session: Session, name: str, cfg: Any) -> list[str]:
    existing = {a.slug: a for a in (await session.execute(select(db.Agent))).scalars()}
    for slug, t in cfg.targets.items():
        fields: dict[str, Any] = dict(
            name=t.display_name or slug,
            team=t.team or name,
            runtime=t.runtime or t.kind,
            model=t.model,
            description=t.description,
            suites=t.suites or sorted(cfg.suites),
            config={"pack": name},
        )
        if slug in existing:
            for k, v in fields.items():
                setattr(existing[slug], k, v)
        else:
            session.add(db.Agent(slug=slug, **fields))
    for agent in await pack_agents(session, name):  # bots dropped from a re-uploaded folder
        if agent.slug not in cfg.targets:
            await session.delete(agent)
    await session.flush()
    return sorted(cfg.targets)


@router.post("/packs", response_model=PackUploaded, status_code=201)
async def upload_pack(body: PackIn, session: Session) -> PackUploaded:
    """Install a folder (provingground.yaml + datasets) and queue one evaluation per bot in it."""
    try:
        staged = packs.stage(body.files, allow_code=get_settings().allow_code_uploads)
    except packs.PackError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        await check_names(session, staged)
    except packs.PackError as exc:
        packs.discard(staged)
        raise HTTPException(409, str(exc)) from exc
    cfg = packs.commit(staged)
    agents = await register(session, staged.name, cfg)
    gate = release_gate(cfg)
    jobs = []
    for slug in agents:
        payload: dict[str, Any] = {"target": slug, "pack": staged.name, "labels": {"source": "upload"}}
        if gate:
            payload["gate"] = gate
        jobs.append(job_out(await queue.enqueue(session, "run", payload)))
    return PackUploaded(name=staged.name, agents=agents, files=packs.files(staged.name), gate=gate, jobs=jobs)


@router.get("/packs", response_model=list[PackOut])
async def list_packs(session: Session) -> list[PackOut]:
    out = []
    for name in packs.names():
        try:
            gate = release_gate(packs.pack_config(name))
        except packs.PackError:
            gate = None
        agents = [a.slug for a in await pack_agents(session, name)]
        out.append(PackOut(name=name, agents=agents, files=packs.files(name), gate=gate))
    return out


@router.delete("/packs/{name}", status_code=204)
async def delete_pack(name: str, session: Session) -> Response:
    """Remove an uploaded folder and its agents from the fleet (their past runs stay in history)."""
    agents = await pack_agents(session, name)
    if not packs.remove(name) and not agents:
        raise HTTPException(404, f"no uploaded folder named {name!r}")
    for agent in agents:
        await session.delete(agent)
    return Response(status_code=204)
