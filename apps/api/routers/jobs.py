"""Jobs and marketplace status (through the port, so GigSim and live Upwork look the same).
When the marketplace is down these answer 503 `marketplace_unavailable`: the UI shows the degraded-mode banner instead of an error page."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from agent.marketplace_port import MarketplaceError

from ..container import Container
from ..db import User
from ..deps import current_user, get_container
from ..errors import from_marketplace
from ._common import market

router = APIRouter(tags=["jobs"])


@router.get("/jobs/search")
async def search_jobs(q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                      _: User = Depends(current_user), c: Container = Depends(get_container)) -> list[dict]:
    try:
        return [j.model_dump() for j in await market(c).search_jobs(q, limit=limit, offset=offset)]
    except MarketplaceError as e:
        raise from_marketplace(e) from e


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, _: User = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    try:
        return (await market(c).get_job(job_id)).model_dump()
    except MarketplaceError as e:
        raise from_marketplace(e) from e


@router.get("/marketplace/status")
async def marketplace_status(_: User = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Connection state + the platform rules/costs currently in force (a policy update shows up here without a deploy)."""
    out: dict[str, Any] = {"status": await c.marketplace_status(), "policy": None, "connects": None}
    if out["status"] == "up":
        try:
            out["policy"] = (await c.market.get_platform_policy()).model_dump()
            out["connects"] = (await c.market.get_connects_balance()).balance
        except MarketplaceError:
            out["status"] = "down"
    return out
