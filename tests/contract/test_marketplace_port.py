"""Contract tests: GigSimAdapter must satisfy MarketplacePort against the local simulator (in-process, no network)."""
import httpx
import pytest

from agent.marketplace_port import (
    Conflict,
    InsufficientConnects,
    MarketplacePort,
    NotFound,
    PolicyRejected,
    ProposalIn,
    RateLimited,
    Unavailable,
)
from gigsim_local.app import create_app
from gigsim_local.config import SimConfig
from gigsim_local.sample_data import sample_world
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter

LETTER = "I built and deployed PyTorch services with FastAPI and measured results. " * 6


def make(token="t", wrap=None, **kw):
    jobs, clients = sample_world()
    app = create_app(SimConfig(token=token, initial_released=99, **kw), jobs, clients)
    transport = httpx.ASGITransport(app=app)
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)
    return GigSimAdapter("http://sim", token, transport=wrap(transport) if wrap else transport, sleep=sleep), sleeps, app


def draft(**kw):
    return ProposalIn(job_id="sample-001", cover_letter=LETTER, bid_amount=900, **kw)


async def test_satisfies_port_and_reads():
    a, _, _ = make()
    assert isinstance(a, MarketplacePort)
    jobs = await a.search_jobs("pytorch")
    assert len(jobs) == 4 and jobs[0].connects_cost >= 8
    job = await a.get_job("sample-001")
    assert job.screening_questions and job.budget.type == "fixed"
    hist = await a.get_client_history(job.client_id)
    assert hist.payment_verified and (await a.get_connects_balance()).balance == 200
    assert (await a.get_platform_policy()).update_active is False
    with pytest.raises(NotFound):
        await a.get_job("missing")


async def test_draft_preview_submit_message_flow():
    a, _, app = make()
    p = await a.create_proposal_draft(draft(), "k1")
    assert p.status == "draft"
    assert (await a.create_proposal_draft(draft(), "k1")).proposal_id == p.proposal_id      # idempotent
    prev = await a.preview_submission(p.proposal_id)
    assert prev.submittable and prev.would_spend == 8 and (await a.get_connects_balance()).balance == 200
    rec = await a.submit_proposal(p.proposal_id, "s1")
    assert rec.balance_after == 192 and (await a.submit_proposal(p.proposal_id, "s1")).balance_after == 192
    with pytest.raises(Conflict):
        await a.submit_proposal(p.proposal_id, "s2")
    app.state.world.tick()
    assert (await a.get_proposal(p.proposal_id)).status in {"replied", "ignored"}
    msgs = await a.list_messages(p.proposal_id)
    assert all(m.sender == "client" for m in msgs)


async def test_policy_update_absorbed_by_data_only():
    a, _, app = make()
    app.state.world.update_active = True
    pol = await a.get_platform_policy()
    assert pol.connects_surcharge == 2 and pol.requires_ai_disclosure
    p = await a.create_proposal_draft(draft(), "k1")
    prev = await a.preview_submission(p.proposal_id)
    assert not prev.submittable and prev.would_spend == 10
    with pytest.raises(PolicyRejected) as e:
        await a.submit_proposal(p.proposal_id, "s")
    assert e.value.rule == "PU-ai-disclosure"


async def test_insufficient_connects_maps():
    a, _, _ = make(initial_connects=1)
    p = await a.create_proposal_draft(draft(), "k1")
    with pytest.raises(InsufficientConnects):
        await a.submit_proposal(p.proposal_id, "s")


class Flaky(httpx.AsyncBaseTransport):
    def __init__(self, inner, fails):
        self.inner, self.fails, self.calls = inner, fails, 0

    async def handle_async_request(self, request):
        self.calls += 1
        if self.calls <= self.fails:
            return httpx.Response(503, headers={"Retry-After": "1"}, json={"detail": {"code": "upstream_error"}})
        return await self.inner.handle_async_request(request)


async def test_retries_transient_5xx_then_succeeds_and_gives_up():
    a, sleeps, _ = make(wrap=lambda t: Flaky(t, 2))
    assert (await a.get_connects_balance()).balance == 200 and sleeps == [1.0, 1.0]
    b, _, _ = make(wrap=lambda t: Flaky(t, 99))
    with pytest.raises(Unavailable):
        await b.get_connects_balance()


async def test_response_lost_after_commit_never_double_spends():
    a, _, _ = make(fault_after_commit_rate=1.0)
    p = None
    for _ in range(1):
        p = await a.create_proposal_draft(draft(), "k1")         # committed, 503, retried with same key
    rec = await a.submit_proposal(p.proposal_id, "s1")
    assert rec.balance_after == 192


async def test_honours_retry_after_on_429():
    t = [0.0]
    a, sleeps, app = make(rate_limit_per_min=2, clock=lambda: t[0])
    a.max_retry_wait = 100.0                                    # default cap is 10s; sim asks for 60s

    async def sleep(s):
        sleeps.append(s)
        t[0] += s
    a._sleep = sleep
    for _ in range(3):
        await a.get_connects_balance()
    assert sleeps and sleeps[0] == 60


async def test_client_side_throttle_stays_under_limit():
    t = [0.0]
    jobs, clients = sample_world()
    app = create_app(SimConfig(token="t", initial_released=99, rate_limit_per_min=3, clock=lambda: t[0]), jobs, clients)
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)
        t[0] += s
    a = GigSimAdapter("http://sim", "t", transport=httpx.ASGITransport(app=app), sleep=sleep, clock=lambda: t[0],
                      max_requests_per_minute=3)
    for _ in range(7):
        await a.get_connects_balance()
    assert slept                                                 # throttled itself; never saw a 429


async def test_rate_limited_raised_when_exhausted():
    a, _, _ = make(rate_limit_per_min=0)
    with pytest.raises(RateLimited):
        await a.get_connects_balance()


def test_factory_modes():
    from agent.marketplace_port import get_marketplace
    with pytest.raises(NotImplementedError):
        get_marketplace("upwork")
    with pytest.raises(ValueError):
        get_marketplace("nope")


def test_factory_builds_gigsim_adapter_from_config(monkeypatch):
    from agent.marketplace_port import get_marketplace
    from agent.settings import get_settings
    monkeypatch.setenv("GIGSIM_BASE_URL", "http://example:1")
    get_settings.cache_clear()
    try:
        assert isinstance(get_marketplace("gigsim"), GigSimAdapter)
    finally:
        get_settings.cache_clear()
