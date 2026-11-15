"""Evidence behind claims (EvidenceHover) for a persona's portfolio. Read-only, auth required."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from agent.runtime import list_personas, persona_dir, persona_visible

from ..container import Container
from ..db import User
from ..deps import current_user, get_container
from ..errors import ApiError
from ..evidence import MAX_IDS, lookup

router = APIRouter(prefix="/personas", tags=["evidence"])


@router.get("/{persona}/evidence")
async def evidence(persona: str, ids: str = Query(..., max_length=2000, description="comma-separated evidence ids"),
                   user: User = Depends(current_user), c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    if persona not in list_personas(c.cfg) or not persona_visible(persona, user.id):
        raise ApiError(404, "persona_not_found", "Unknown persona")
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:MAX_IDS]
    return await asyncio.to_thread(lookup, persona_dir(persona, c.cfg), wanted)
