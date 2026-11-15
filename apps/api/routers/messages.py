"""Client conversation threads of the user's own proposals. Replying is a `conversation` run (POST /runs) that ends at the human gate;
nothing is ever sent from here."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from agent.marketplace_port import MarketplaceError

from ..container import Container
from ..db import User
from ..deps import current_user, get_container, get_service
from ..errors import from_marketplace
from ..service import RunService
from ._common import market, own_proposal_run

router = APIRouter(tags=["messages"])


@router.get("/proposals/{proposal_id}/messages")
async def list_messages(proposal_id: str, user: User = Depends(current_user), svc: RunService = Depends(get_service),
                        c: Container = Depends(get_container)) -> list[dict]:
    await own_proposal_run(svc, user, proposal_id)
    try:
        return [m.model_dump() for m in await market(c).list_messages(proposal_id)]
    except MarketplaceError as e:
        raise from_marketplace(e) from e
