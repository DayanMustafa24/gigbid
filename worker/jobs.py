"""Scheduled background work (pure logic, no Redis import: testable offline). `worker/main.py` wires these into Arq.

Nothing here can send, spend or message anyone: the scout only STARTS bid runs, which stop at the human approval gate. Every function is
idempotent (starting the same job twice returns the active run; outcome sync is forward-only), so a retried or doubled task is harmless."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from agent.marketplace_port import MarketplaceError
from apps.api.container import Container
from apps.api.db import User, UserSetting
from apps.api.errors import ApiError
from apps.api.schemas import RunCreate
from apps.api.service import RunService

log = logging.getLogger("gigbid.worker")


def schedule(every_minutes: int) -> dict[str, Any]:
    """Arq cron kwargs for 'every N minutes' (N < 60) or 'every N/60 hours, on the hour'."""
    n = max(1, int(every_minutes))
    if n < 60:
        return {"minute": set(range(0, 60, n))}
    return {"hour": set(range(0, 24, max(1, n // 60))), "minute": {0}}


async def scout_user(c: Container, svc: RunService, owner: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Search the marketplace for the user's saved queries, start bid runs for jobs not seen before (capped per pass)."""
    persona, queries = prefs.get("default_persona"), [q for q in prefs.get("scout_queries", []) if q]
    out: dict[str, Any] = {"owner": owner, "found": 0, "started": [], "errors": 0}
    if not persona or not queries or c.market is None:
        return {**out, "skipped": "needs a default persona, saved queries and a marketplace"}
    wcfg = c.cfg.get("worker", {})
    seen = {r.subject_id for r in await svc.list_runs(owner, kind="bid", limit=500)}
    fresh: dict[str, str] = {}
    for q in queries:
        try:
            hits = await c.market.search_jobs(q, limit=int(wcfg.get("scout_limit_per_query", 10)))
        except MarketplaceError as e:
            log.warning("scout search failed (%s): %s", type(e).__name__, q)
            out["errors"] += 1
            continue
        for h in hits:
            if h.job_id not in seen and h.job_id not in fresh:
                fresh[h.job_id] = h.title
    out["found"] = len(fresh)
    for job_id in list(fresh)[: int(wcfg.get("max_runs_per_scout", 5))]:
        try:
            run, created = await svc.start(owner, RunCreate(kind="bid", persona=persona, job_id=job_id))
            if created:
                out["started"].append(run.id)
        except (ApiError, MarketplaceError) as e:
            log.warning("scout could not start a run for %s: %s", job_id, getattr(e, "code", type(e).__name__))
            out["errors"] += 1
    return out


async def scout_all(c: Container, svc: RunService) -> list[dict[str, Any]]:
    async with c.sessions() as db:
        rows = (await db.execute(select(UserSetting.owner_id, UserSetting.data))).all()
    return [await scout_user(c, svc, owner, data or {}) for owner, data in rows]


async def sync_all(c: Container, svc: RunService) -> list[dict[str, Any]]:
    if c.market is None:
        return []
    async with c.sessions() as db:
        owners = list((await db.execute(select(User.id))).scalars())
    out = []
    for owner in owners:
        try:
            out.append({"owner": owner, **await svc.sync_outcomes(owner)})
        except ApiError as e:  # marketplace down: try again at the next tick
            out.append({"owner": owner, "error": e.code})
    return out
