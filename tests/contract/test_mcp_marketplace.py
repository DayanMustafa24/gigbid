"""The product path end to end, in process: McpMarketplace -> McpGateway (per-node allowlists) -> Marketplace MCP server -> GigSimAdapter -> gigsim_local.
Proves the agents reach the marketplace ONLY through MCP, that spending writes need a human grant, and that errors keep their meaning."""
import httpx
import pytest

from agent.approvals import ApprovalError, ApprovalGrant, message_subject, text_sha
from agent.marketplace_port import (
    ApprovalRequired,
    MarketplacePort,
    NotFound,
    PolicyRejected,
    ProposalIn,
    Unavailable,
)
from agent.mcp_client import InMemoryTransport, McpGateway, McpNotAllowed
from agent.mcp_marketplace import McpMarketplace, build_agent_marketplace
from agent.nodes.common import Deps, approval_kwargs
from agent.settings import load_config
from gigsim_local.app import create_app
from gigsim_local.config import SimConfig
from gigsim_local.sample_data import sample_world
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter

SECRET = "test-secret-0123456789"
LETTER = "I built and deployed PyTorch services with FastAPI and measured results. " * 6


async def _nosleep(_):
    return None


def build(tmp_path, **sim):
    from mcp_servers.marketplace.server import create_server

    jobs, clients = sample_world()
    app = create_app(SimConfig(token="t", initial_released=99, **sim), jobs, clients)
    adapter = GigSimAdapter("http://sim", "t", transport=httpx.ASGITransport(app=app), sleep=_nosleep)
    server = create_server(adapter, audit_path=tmp_path / "audit.jsonl", secret=SECRET)
    gateway = McpGateway(InMemoryTransport({"marketplace": server}), load_config()["mcp"]["allowlists"], max_retries=0, sleep=_nosleep)
    return McpMarketplace(gateway), app


def grant(**kw) -> Deps:
    return Deps(approval=ApprovalGrant("owner-1", "run-1", secret=SECRET, **kw))


def draft(job="sample-001", amount=900, **kw) -> ProposalIn:
    return ProposalIn(job_id=job, cover_letter=LETTER, bid_amount=amount, **kw)


async def test_reads_go_through_mcp_and_return_port_models(tmp_path):
    m, _ = build(tmp_path)
    assert isinstance(m, MarketplacePort)
    jobs = await m.search_jobs("pytorch")
    assert len(jobs) == 4
    job = await m.get_job("sample-001")
    assert job.screening_questions and (await m.get_client_history(job.client_id)).payment_verified
    assert (await m.get_connects_balance()).balance == 200 and (await m.get_platform_policy()).update_active is False
    with pytest.raises(NotFound):
        await m.get_job("missing")


async def test_submit_needs_a_human_grant_then_is_idempotent(tmp_path):
    m, _ = build(tmp_path)
    p = await m.create_proposal_draft(draft(), "draft-key-0001")
    prev = await m.preview_submission(p.proposal_id)
    assert prev.submittable and prev.would_spend == 8 and (await m.get_connects_balance()).balance == 200
    with pytest.raises(ApprovalRequired):                                   # no token: refused by the SERVER
        await m.submit_proposal(p.proposal_id, "submit-key-0001")
    assert approval_kwargs(Deps(), "submit_proposal", p.proposal_id) == {}   # no grant, nothing to mint with
    kw = approval_kwargs(grant(), "submit_proposal", p.proposal_id, LETTER)
    rec = await m.submit_proposal(p.proposal_id, "submit-key-0001", **kw)
    assert rec.balance_after == 192
    again = await m.submit_proposal(p.proposal_id, "submit-key-0001", **kw)  # retry, same key: no second spend
    assert again.balance_after == 192 and (await m.get_connects_balance()).balance == 192


async def test_token_is_bound_to_its_proposal(tmp_path):
    m, _ = build(tmp_path)
    a = await m.create_proposal_draft(draft("sample-001"), "draft-key-000A")
    b = await m.create_proposal_draft(draft("sample-006", 30), "draft-key-000B")
    token_for_a = approval_kwargs(grant(), "submit_proposal", a.proposal_id, LETTER)
    with pytest.raises(ApprovalRequired):
        await m.submit_proposal(b.proposal_id, "submit-key-000B", **token_for_a)


def test_grant_for_the_unedited_draft_refuses_other_text():
    g = ApprovalGrant("owner-1", "run-1", approved_sha=text_sha("the text the human saw"), secret=SECRET)
    assert g.mint("send_message", "s", text="the text the human saw")
    with pytest.raises(ApprovalError):
        g.mint("send_message", "s", text="something else")


async def test_send_message_token_is_bound_to_the_exact_text(tmp_path):
    m, app = build(tmp_path)
    p = await m.create_proposal_draft(draft("sample-004", 100), "draft-key-0004")        # scam client: always answers
    await m.submit_proposal(p.proposal_id, "submit-key-0004", **approval_kwargs(grant(), "submit_proposal", p.proposal_id))
    app.state.world.tick()
    text = "Thanks. I will keep all communication on the platform."
    with pytest.raises(ApprovalRequired):
        await m.send_message(p.proposal_id, text, "msg-key-00001")
    other = approval_kwargs(grant(), "send_message", message_subject(p.proposal_id, "a different text"))
    with pytest.raises(ApprovalRequired):                                   # approved text A cannot carry text B
        await m.send_message(p.proposal_id, text, "msg-key-00001", **other)
    ok = approval_kwargs(grant(), "send_message", message_subject(p.proposal_id, text), text)
    assert (await m.send_message(p.proposal_id, text, "msg-key-00001", **ok)).sender == "freelancer"


async def test_policy_update_surfaces_as_a_typed_error_with_its_rule(tmp_path):
    m, app = build(tmp_path)
    app.state.world.update_active = True
    p = await m.create_proposal_draft(draft(), "draft-key-0001")
    assert not (await m.preview_submission(p.proposal_id)).submittable
    with pytest.raises(PolicyRejected) as e:
        await m.submit_proposal(p.proposal_id, "submit-key-0001", **approval_kwargs(grant(), "submit_proposal", p.proposal_id))
    assert e.value.rule == "PU-ai-disclosure"


async def test_allowlist_is_enforced_for_the_caller_a_method_runs_as(tmp_path):
    m, _ = build(tmp_path)
    p = await m.create_proposal_draft(draft(), "draft-key-0001")
    llm_node = McpMarketplace(m.gateway, callers={"submit_proposal": "analyst"})   # a tool-less node must never reach a write
    with pytest.raises(McpNotAllowed):
        await llm_node.submit_proposal(p.proposal_id, "submit-key-0001")


async def test_dead_server_is_a_typed_unavailable_not_a_crash():
    class Down:
        async def call(self, *a, **k):
            raise ConnectionError("refused")

    gw = McpGateway(Down(), load_config()["mcp"]["allowlists"], max_retries=0, sleep=_nosleep)
    with pytest.raises(Unavailable):
        await McpMarketplace(gw).get_connects_balance()
    with pytest.raises(Unavailable):                                         # second call fails fast (circuit open)
        await McpMarketplace(gw).get_connects_balance()
    assert gw.health()["marketplace"]["up"] is False


def test_access_mode_switch(monkeypatch):
    cfg = {"marketplace": {"access": "mcp"}}
    assert isinstance(build_agent_marketplace(cfg, gateway=McpGateway(InMemoryTransport({}), {})), McpMarketplace)
    monkeypatch.setenv("MARKETPLACE_ACCESS", "bogus")
    with pytest.raises(ValueError):
        build_agent_marketplace(cfg)
