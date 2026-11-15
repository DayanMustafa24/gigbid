"""Analytics: the numbers the dashboard shows, computed from the run ledger (never invented). Compute time excludes the time a run
waits for the human: latency = sum of node durations from the trace."""
from __future__ import annotations

import asyncio
import math
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from agent.memory.stats import two_proportion_z

from ..container import Container
from ..db import RunEvent, User
from ..deps import current_user, get_container, get_service
from ..service import RunService

router = APIRouter(prefix="/analytics", tags=["analytics"])


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no data (never a made-up zero)."""
    if not values:
        return None
    s = sorted(values)
    return round(s[max(0, math.ceil(p * len(s)) - 1)], 3)


def strategy_experiment(by_persona: dict[str, dict[str, dict[str, int]]]) -> dict[str, Any]:
    """X2 proposal experiments: pool measured outcomes per opener strategy over personas and test the reply-rate difference of two arms.
    Raw counts and a pooled two-proportion z-test; no estimate is made up (arms with no outcomes are simply absent)."""
    arms: dict[str, dict[str, int]] = {}
    for table in by_persona.values():
        for name, t in table.items():
            a = arms.setdefault(name, {"n": 0, "replied": 0, "won": 0})
            for k in a:
                a[k] += int(t.get(k, 0))
    out: dict[str, Any] = {"arms": arms, "test": None}
    if len(arms) == 2:
        (na, a), (nb, b) = sorted(arms.items())
        z, p = two_proportion_z(a["replied"], a["n"], b["replied"], b["n"])
        out["test"] = {"metric": "reply_rate", "a": na, "b": nb, "z": round(z, 4), "p_value": round(p, 4), "significant_at_0.05": p < 0.05}
    return out


@router.get("/summary")
async def summary(persona: str | None = Query(None, max_length=60), limit: int = Query(500, ge=1, le=1000),
                  user: User = Depends(current_user), svc: RunService = Depends(get_service), c: Container = Depends(get_container)) -> dict[str, Any]:
    runs = [r for r in await svc.list_runs(user.id, limit=limit) if persona in (None, r.persona)]
    bids = [r for r in runs if r.kind == "bid"]
    sums = [r.summary or {} for r in bids]
    approved = ("approved", "edited", "submitted")
    funnel = {
        "analysed": len(bids),
        "bid": sum(1 for s in sums if s.get("decision") == "bid"),
        "drafted": sum(1 for r, s in zip(bids, sums, strict=True) if s.get("price") and (r.status == "awaiting_approval" or s.get("approval_status"))),
        "approved": sum(1 for s in sums if s.get("approval_status") in approved),
        "submitted": sum(1 for s in sums if s.get("approval_status") == "submitted"),
    }
    by_category: dict[str, dict[str, int]] = {}
    for s in sums:  # measured from the run ledger; runs of jobs without a category are grouped as 'uncategorised'
        row = by_category.setdefault(((s.get("job") or {}).get("category")) or "uncategorised", {"analysed": 0, "bid": 0, "submitted": 0})
        row["analysed"] += 1
        row["bid"] += s.get("decision") == "bid"
        row["submitted"] += s.get("approval_status") == "submitted"
    cat_outcomes = await asyncio.to_thread(c.memory.category_table, user.id, persona)
    for cat, t in cat_outcomes.items():  # measured marketplace outcomes per category; absent until the marketplace reports some
        row = by_category.setdefault(cat, {"analysed": 0, "bid": 0, "submitted": 0})
        row.update({"outcomes": t["n"], "replied": t["replied"], "won": t["won"]})
    compute: dict[str, float] = {}
    ids = [r.id for r in runs]
    async with c.sessions() as db:
        for i in range(0, len(ids), 400):  # stay under SQLite's bound-variable limit
            rows = (await db.execute(select(RunEvent.run_id, RunEvent.data).where(RunEvent.run_id.in_(ids[i:i + 400])))).all()
            for rid, data in rows:
                compute[rid] = compute.get(rid, 0.0) + float((data or {}).get("ms", 0) or 0) / 1000.0
    finished = [compute[r.id] for r in runs if r.status in ("completed", "skipped", "closed", "awaiting_approval") and r.id in compute]
    tokens = sum(((r.summary or {}).get("usage") or {}).get("tokens_in", 0) + ((r.summary or {}).get("usage") or {}).get("tokens_out", 0) for r in runs)
    cost = sum(((r.summary or {}).get("usage") or {}).get("cost_usd", 0) for r in runs)
    personas = sorted({r.persona for r in runs})
    outcomes = {p: await asyncio.to_thread(c.memory.outcome_stats, user.id, p) for p in personas}
    strategies = {p: await asyncio.to_thread(c.memory.strategy_table, user.id, p) for p in personas}
    experiment = strategy_experiment(strategies)
    return {"runs": {"total": len(runs), "by_status": dict(Counter(r.status for r in runs)), "by_kind": dict(Counter(r.kind for r in runs))},
            "funnel": funnel, "by_category": by_category,
            "compute_latency_s": {"n": len(finished), "p50": percentile(finished, 0.5), "p95": percentile(finished, 0.95)},
            "usage": {"tokens": int(tokens), "cost_usd": round(float(cost), 6)},
            "outcomes": outcomes, "strategies": strategies, "experiment": experiment}
