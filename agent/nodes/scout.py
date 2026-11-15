"""Scout: searches the marketplace (read-only port) for saved queries and drops jobs already seen. Never writes, never calls an LLM.

`deps.market` is only given to this node (and the executor), so analyst/writer cannot reach the marketplace even by accident.
Duplicate detection here is by job_id and near-identical snippet; the Risk Assessor repeats the stronger description check later.
"""
from __future__ import annotations

import time
from typing import Any

from .common import Deps, fail, finish, jaccard


async def scout(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        if deps.market is None:
            raise RuntimeError("scout needs a marketplace port in Deps")
        queries = [q for q in (state.get("queries") or []) if q]
        seen_ids = {j.get("job_id") for j in deps.known_jobs()}
        seen_text = [j.get("description", "") or j.get("snippet", "") for j in deps.known_jobs()]
        thr = deps.cfg["risk"]["duplicate_similarity"]
        found: dict[str, dict[str, Any]] = {}
        for q in queries:
            for s in await deps.market.search_jobs(q, limit=int(state.get("limit", 20))):
                d = s.model_dump()
                if d["job_id"] in seen_ids or d["job_id"] in found:
                    continue
                if any(jaccard(d.get("snippet", ""), t) >= thr for t in seen_text if t):
                    continue
                found[d["job_id"]] = d
        return finish("scout", t0, {"candidates": list(found.values())}, mode="deterministic", queries=len(queries), found=len(found))
    except Exception as e:
        return fail("scout", t0, e)
