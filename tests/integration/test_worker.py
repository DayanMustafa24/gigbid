"""Phase 6 worker logic: cron schedule maths, scouting (starts runs that stop at the human gate, never twice for the same job),
degraded marketplace, outcome sync with no proposals. Pure logic: no Redis needed."""
from __future__ import annotations

from agent.marketplace_port import MarketplaceError
from tests.conftest import FakeMarket, market_job
from worker.jobs import schedule, scout_all, scout_user, sync_all

PREFS = {"default_persona": "ai_ml", "scout_queries": ["rag"]}


def test_schedule_maths():
    assert schedule(15) == {"minute": {0, 15, 30, 45}}
    assert schedule(60) == {"hour": set(range(24)), "minute": {0}}
    assert schedule(240) == {"hour": {0, 4, 8, 12, 16, 20}, "minute": {0}}
    assert schedule(0) == {"minute": set(range(60))}  # clamped to 1 minute, never a crash


async def test_scout_starts_a_run_once_and_stops_at_the_gate(api_harness):
    h = await api_harness(market=FakeMarket([market_job("j1"), market_job("j2")]))
    owner = "u1"
    first = await scout_user(h.c, h.svc, owner, PREFS)
    await h.svc.drain()
    assert first["found"] == 2 and len(first["started"]) == 2 and first["errors"] == 0
    runs = await h.svc.list_runs(owner, kind="bid", limit=10)
    assert {r.status for r in runs} <= {"awaiting_approval", "skipped"}  # nothing is submitted without a human
    second = await scout_user(h.c, h.svc, owner, PREFS)
    assert second["found"] == 0 and second["started"] == []  # already seen: idempotent


async def test_scout_respects_cap_and_needs_setup(api_harness):
    h = await api_harness(market=FakeMarket([market_job(f"j{i}") for i in range(8)]))
    cap = int(h.c.cfg["worker"]["max_runs_per_scout"])
    res = await scout_user(h.c, h.svc, "u1", PREFS)
    await h.svc.drain()
    assert len(res["started"]) == cap
    assert "skipped" in await scout_user(h.c, h.svc, "u1", {})


async def test_scout_survives_a_failing_marketplace(api_harness):
    class Broken(FakeMarket):
        async def search_jobs(self, q, limit=10, offset=0):
            raise MarketplaceError("boom")

    h = await api_harness(market=Broken())
    res = await scout_user(h.c, h.svc, "u1", PREFS)
    assert res["errors"] == 1 and res["started"] == []


async def test_scout_all_and_sync_all_with_no_users_or_proposals(api_harness):
    h = await api_harness(market=FakeMarket())
    assert await scout_all(h.c, h.svc) == []
    assert await sync_all(h.c, h.svc) == []
    none = await api_harness(market=None, name="nomarket")
    assert await sync_all(none.c, none.svc) == []
