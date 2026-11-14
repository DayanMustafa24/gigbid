"""FastAPI surface of the local GigSim stand-in.  Run: uv run python -m gigsim_local   (http://localhost:8081/docs)"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from pydantic import BaseModel, Field

from .config import SimConfig
from .world import World


class ProposalIn(BaseModel):
    job_id: str
    cover_letter: str = Field(min_length=1, max_length=5000)
    screening_answers: list[str] = Field(default_factory=list, max_length=20)
    bid_amount: float = Field(ge=0)
    bid_type: Literal["fixed", "hourly"] = "fixed"
    ai_disclosure: bool = False


class MessageIn(BaseModel):
    proposal_id: str
    text: str = Field(min_length=1, max_length=4000)


class ResetIn(BaseModel):
    persona: str | None = None


class PolicyToggle(BaseModel):
    enabled: bool


class FaultsIn(BaseModel):
    fault_rate: float = Field(0.0, ge=0, le=1)
    fault_after_commit_rate: float = Field(0.0, ge=0, le=1)


def create_app(cfg: SimConfig | None = None, jobs: list[dict] | None = None, clients: list[dict] | None = None) -> FastAPI:
    cfg = cfg or SimConfig.from_env()
    world = World(cfg, jobs, clients)
    app = FastAPI(title="GigSim (local stand-in)", version="0.1.0")
    app.state.world = world

    def w(request: Request) -> World:
        return request.app.state.world

    def auth_only(request: Request, authorization: str | None = Header(None)) -> World:
        world_ = w(request)
        world_.check_auth(authorization)
        return world_

    def guard(request: Request, authorization: str | None = Header(None)) -> World:
        """auth -> rate limit -> random 5xx, in that order, for every marketplace route."""
        world_ = auth_only(request, authorization)
        world_.rate_limit()
        world_.maybe_fault(world_.fault_rate)
        return world_

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "service": "gigsim-local"}

    # ---- marketplace surface (what adapters call) -----------------------------------------------------------
    @app.get("/v1/platform/policy")
    def platform_policy(world_: World = Depends(guard)) -> dict:
        return world_.platform_policy()

    @app.get("/v1/jobs")
    def search_jobs(q: str = "", limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                    world_: World = Depends(guard)) -> dict:
        return world_.search(q, limit, offset)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, world_: World = Depends(guard)) -> dict:
        return world_.get_job(job_id)

    @app.get("/v1/clients/{client_id}/history")
    def client_history(client_id: str, world_: World = Depends(guard)) -> dict:
        return world_.client_history(client_id)

    @app.get("/v1/connects/balance")
    def connects_balance(world_: World = Depends(guard)) -> dict:
        return {"balance": world_.connects}

    @app.post("/v1/proposals", status_code=201)
    def create_proposal(body: ProposalIn, idempotency_key: str | None = Header(None), world_: World = Depends(guard)) -> dict:
        result, replay = world_.create_proposal(body.model_dump(), idempotency_key)
        if not replay:
            world_.maybe_fault(world_.fault_after_commit_rate)   # committed, but the response is "lost"
        return result

    @app.get("/v1/proposals/{proposal_id}")
    def get_proposal(proposal_id: str, world_: World = Depends(guard)) -> dict:
        return world_.public_proposal(world_.proposal(proposal_id))

    @app.post("/v1/proposals/{proposal_id}/submit")
    def submit_proposal(proposal_id: str, dry_run: bool = False, idempotency_key: str | None = Header(None),
                        world_: World = Depends(guard)) -> dict:
        if dry_run:
            return world_.preview_submit(proposal_id)
        result, replay = world_.submit(proposal_id, idempotency_key)
        if not replay:
            world_.maybe_fault(world_.fault_after_commit_rate)
        return result

    @app.get("/v1/messages")
    def list_messages(proposal_id: str, world_: World = Depends(guard)) -> dict:
        return {"items": world_.list_messages(proposal_id)}

    @app.post("/v1/messages", status_code=201)
    def send_message(body: MessageIn, idempotency_key: str | None = Header(None), world_: World = Depends(guard)) -> dict:
        result, replay = world_.send_message(body.model_dump(), idempotency_key)
        if not replay:
            world_.maybe_fault(world_.fault_after_commit_rate)
        return result

    # ---- admin (evals / demos; bearer-protected, no rate limit, no faults) ------------------------------------
    @app.post("/admin/reset")
    def admin_reset(body: ResetIn, world_: World = Depends(auth_only)) -> dict:
        world_.reset(body.persona)
        return {"ok": True, "persona": body.persona, "released": world_.released, "feed": len(world_.feed)}

    @app.post("/admin/tick")
    def admin_tick(n: int = Query(1, ge=1, le=1000), world_: World = Depends(auth_only)) -> dict:
        world_.tick(n)
        return {"tick": world_.tick_no, "released": world_.released}

    @app.post("/admin/policy-update")
    def admin_policy(body: PolicyToggle, world_: World = Depends(auth_only)) -> dict:
        world_.update_active = body.enabled
        return world_.platform_policy()

    @app.post("/admin/faults")
    def admin_faults(body: FaultsIn, world_: World = Depends(auth_only)) -> dict:
        world_.fault_rate, world_.fault_after_commit_rate = body.fault_rate, body.fault_after_commit_rate
        return body.model_dump()

    @app.get("/admin/outcomes")
    def admin_outcomes(world_: World = Depends(auth_only)) -> dict:
        items = world_.outcomes()
        n = len(items)
        sub = [o for o in items if o["status"] != "draft"]
        replied = [o for o in sub if o["status"] in {"replied", "interview", "hired", "closed"}]
        return {"items": items, "submitted": len(sub), "reply_rate": len(replied) / len(sub) if sub else None,
                "hire_rate": sum(o["status"] == "hired" for o in sub) / len(sub) if sub else None, "total": n}

    return app
