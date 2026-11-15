"""The user's own proposals: the ledger of what GigBid submitted for them, live status from the marketplace, and outcome sync."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from agent.marketplace_port import MarketplaceError

from ..container import Container
from ..db import User
from ..deps import current_user, get_container, get_service, run_budget
from ..errors import from_marketplace
from ..service import RunService
from ._common import market, own_proposal_run

router = APIRouter(tags=["proposals"])


@router.get("/proposals")
async def list_proposals(user: User = Depends(current_user), svc: RunService = Depends(get_service)) -> list[dict]:
    """Proposals this user submitted through GigBid (from the run ledger), newest first."""
    return [{"proposal_id": r.summary["submission"]["proposal_id"], "run_id": r.id, "job_id": r.subject_id, "persona": r.persona,
             "title": (r.summary.get("job") or {}).get("title"), "submission": r.summary["submission"], "strategy": r.summary.get("strategy"),
             "submitted_at": r.updated_at.isoformat()} for r in await svc.proposal_runs(user.id)]


@router.post("/proposals/sync")
async def sync_outcomes(user: User = Depends(run_budget), svc: RunService = Depends(get_service)) -> dict:
    """Pull the marketplace status of every submitted proposal into outcome memory (forward-only: a win is never overwritten)."""
    return await svc.sync_outcomes(user.id)


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, user: User = Depends(current_user), svc: RunService = Depends(get_service),
                       c: Container = Depends(get_container)) -> dict:
    await own_proposal_run(svc, user, proposal_id)
    try:
        return (await market(c).get_proposal(proposal_id)).model_dump()
    except MarketplaceError as e:
        raise from_marketplace(e) from e
