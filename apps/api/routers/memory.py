"""Memory screen API (R7): what the system learned about the user's style and outcomes, viewable and deletable. Owner-scoped.
Nothing here writes new memory: items arrive only through the narrow doors in agent/memory (a human's edit/approval, a marketplace outcome)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Response

from ..container import Container
from ..db import User
from ..deps import current_user, get_container
from ..errors import ApiError
from ..schemas import ForgetIn

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("")
async def list_items(persona: str | None = Query(None, max_length=60), include_inactive: bool = False,
                     user: User = Depends(current_user), c: Container = Depends(get_container)) -> list[dict]:
    return await asyncio.to_thread(c.memory.list_items, user.id, persona, include_inactive)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: int, user: User = Depends(current_user), c: Container = Depends(get_container)) -> Response:
    """Soft-delete with a tombstone: a deleted rule is never silently learned again."""
    if not await asyncio.to_thread(c.memory.delete_item, user.id, item_id):
        raise ApiError(404, "memory_item_not_found", "Memory item not found")
    return Response(status_code=204)


@router.post("/forget")
async def forget_all(body: ForgetIn, user: User = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Hard-delete everything remembered about this user (style events, rules, outcomes). The audit log keeps only that it happened."""
    return await asyncio.to_thread(c.memory.forget_all, user.id)


@router.get("/log")
async def audit_log(limit: int = Query(100, ge=1, le=500), user: User = Depends(current_user), c: Container = Depends(get_container)) -> list[dict]:
    return await asyncio.to_thread(c.memory.audit_log, user.id, limit)


@router.get("/outcomes")
async def outcomes(persona: str = Query(..., max_length=60), user: User = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Measured reply/win counts and the per-opener-strategy table (X2 experiments)."""
    cfg = c.cfg.get("pricing", {})
    return {"persona": persona,
            "overall": await asyncio.to_thread(c.memory.outcome_stats, user.id, persona),
            "measured": await asyncio.to_thread(c.memory.measured_rates, user.id, persona,
                                                float(cfg.get("base_reply_rate", 0.3)), float(cfg.get("win_given_reply", 0.25))),
            "strategies": await asyncio.to_thread(c.memory.strategy_table, user.id, persona)}
