"""The approval queue: everything waiting for a human decision (proposals to submit, client replies to send), newest first.

The decision itself is `POST /runs/{run_id}/decision`: the single approval point. It creates the in-process approval grant that
lets the deterministic executor/sender mint a token for exactly what the human approved (agent/approvals.py). There is no endpoint
that issues tokens, by design.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..db import User
from ..deps import current_user, get_service
from ..schemas import RunOut
from ..service import RunService

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[RunOut])
async def pending_approvals(kind: Literal["bid", "conversation"] | None = None, limit: int = Query(50, ge=1, le=200),
                            user: User = Depends(current_user), svc: RunService = Depends(get_service)):
    return await svc.list_runs(user.id, status="awaiting_approval", kind=kind, limit=limit)
