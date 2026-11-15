"""Helpers shared by the marketplace-facing routers (jobs, proposals, messages)."""
from __future__ import annotations

from typing import Any

from ..container import Container
from ..db import Run, User
from ..errors import ApiError
from ..service import RunService


def market(c: Container) -> Any:
    """The connected marketplace (through the port, MCP path by default) or a 503 the UI turns into the degraded-mode banner."""
    if c.market is None:
        raise ApiError(503, "marketplace_unavailable", "No marketplace is configured", retryable=True)
    return c.market


async def own_proposal_run(svc: RunService, user: User, proposal_id: str) -> Run:
    """The run that submitted this proposal for THIS user. Another user's proposal is indistinguishable from a missing one (404)."""
    run = next((r for r in await svc.proposal_runs(user.id) if r.summary["submission"]["proposal_id"] == proposal_id), None)
    if run is None:
        raise ApiError(404, "proposal_not_found", "Proposal not found")
    return run
