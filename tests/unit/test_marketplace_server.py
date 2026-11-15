"""Marketplace MCP server: approval gating, dry-run, idempotency, audit chain, rate limit, bearer auth.
Runs in-process: in-memory MCP client <-> server <-> GigSimAdapter <-> gigsim_local ASGI app (no network)."""
import json

import httpx
import pytest

from agent.approvals import ApprovalError, issue, message_subject, verify
from gigsim_local.app import create_app
from gigsim_local.config import SimConfig
from gigsim_local.sample_data import sample_world
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.guard import AuditLog, BearerAuthMiddleware, RateLimiter, ToolRateLimited

SECRET = "test-secret-0123456789"
LETTER = "I built and deployed PyTorch services with FastAPI and measured results. " * 6
DRAFT = {"job_id": "sample-001", "cover_letter": LETTER, "bid_amount": 900}


async def _nosleep(_):
    return None


def build(tmp_path, **kw):
    from mcp_servers.marketplace.server import create_server

    jobs, clients = sample_world()
    app = create_app(SimConfig(token="t", initial_released=99), jobs, clients)
    adapter = GigSimAdapter("http://sim", "t", transport=httpx.ASGITransport(app=app), sleep=_nosleep)
    return create_server(adapter, audit_path=tmp_path / "audit.jsonl", secret=SECRET, **kw)


def connect(server):
    memory = pytest.importorskip("mcp.shared.memory")
    return memory.create_connected_server_and_client_session(server._mcp_server)


def text(res) -> str:
    return " ".join(getattr(c, "text", "") for c in res.content)


# ---- approvals (pure) ------------------------------------------------------------------------------------------


def test_approval_token_binding_expiry_and_forgery():
    tok = issue("submit_proposal", "prop-1", secret=SECRET, now=1000, ttl=60)
    assert verify(tok, "submit_proposal", "prop-1", secret=SECRET, now=1030).approver == "human"
    for bad in (
        lambda: verify(tok, "submit_proposal", "prop-2", secret=SECRET, now=1030),      # other subject
        lambda: verify(tok, "send_message", "prop-1", secret=SECRET, now=1030),         # other action
        lambda: verify(tok, "submit_proposal", "prop-1", secret=SECRET, now=1061),      # expired
        lambda: verify(tok, "submit_proposal", "prop-1", secret="other-secret", now=1030),  # wrong key
        lambda: verify(None, "submit_proposal", "prop-1", secret=SECRET),
        lambda: verify("garbage.token", "submit_proposal", "prop-1", secret=SECRET),
    ):
        with pytest.raises(ApprovalError):
            bad()


def test_message_subject_changes_with_text():
    assert message_subject("p", "hello") != message_subject("p", "hello!")


# ---- guards (pure) ---------------------------------------------------------------------------------------------


def test_rate_limiter_sliding_window():
    t = [0.0]
    rl = RateLimiter(2, clock=lambda: t[0])
    rl.check("a")
    rl.check("a")
    with pytest.raises(ToolRateLimited):
        rl.check("a")
    rl.check("b")          # other caller unaffected
    t[0] = 61
    rl.check("a")


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append(tool="x", outcome="ok", args={"api_key": "gsk_abcdefghijklmnopqrstuv", "note": "mail me a@b.co"})
    log.append(tool="y", outcome="denied")
    assert log.verify() and AuditLog(tmp_path / "a.jsonl").verify()
    raw = (tmp_path / "a.jsonl").read_text(encoding="utf-8")
    assert "gsk_abc" not in raw and "a@b.co" not in raw        # masked before hitting disk
    (tmp_path / "a.jsonl").write_text(raw.replace('"denied"', '"ok"'), encoding="utf-8")
    assert not AuditLog(tmp_path / "a.jsonl").verify()


async def test_bearer_middleware():
    async def inner(scope, receive, send):
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    transport = httpx.ASGITransport(app=BearerAuthMiddleware(inner, "tok"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/mcp")).status_code == 401
        assert (await c.get("/mcp", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/mcp", headers={"Authorization": "Bearer tok"})).status_code == 200
        assert (await c.get("/healthz")).status_code == 200


# ---- server over in-memory MCP ---------------------------------------------------------------------------------


async def test_tools_and_annotations(tmp_path):
    async with connect(build(tmp_path)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    reads = {"search_jobs", "get_job", "get_client_history", "get_connects_balance", "get_platform_policy",
             "get_proposal", "list_messages"}
    writes = {"create_proposal_draft", "submit_proposal", "send_message"}
    assert set(tools) == reads | writes
    assert all(tools[n].annotations.readOnlyHint for n in reads)
    assert all(not tools[n].annotations.readOnlyHint for n in writes)
    assert tools["submit_proposal"].annotations.destructiveHint and tools["send_message"].annotations.destructiveHint
    assert not tools["create_proposal_draft"].annotations.destructiveHint
    assert all(t.outputSchema for t in tools.values())


async def test_reads_and_error_mapping(tmp_path):
    async with connect(build(tmp_path)) as c:
        jobs = await c.call_tool("search_jobs", {"query": "pytorch", "limit": 2})
        assert not jobs.isError and len(jobs.structuredContent["result"]) == 2
        assert (await c.call_tool("get_connects_balance", {})).structuredContent["balance"] == 200
        bad = await c.call_tool("get_job", {"job_id": "missing"})
        assert bad.isError and "NotFound" in text(bad) and "retryable=false" in text(bad)


async def test_submit_requires_human_approval_and_is_idempotent(tmp_path):
    server = build(tmp_path)
    async with connect(server) as c:
        d = await c.call_tool("create_proposal_draft", {"draft": DRAFT, "idempotency_key": "draft-key-1"})
        pid = d.structuredContent["proposal_id"]
        args = {"proposal_id": pid, "idempotency_key": "submit-key-1"}

        dry = await c.call_tool("submit_proposal", {**args, "dry_run": True})            # no token needed, spends nothing
        assert dry.structuredContent["preview"]["would_spend"] == 8 and dry.structuredContent["receipt"] is None
        assert (await c.call_tool("get_connects_balance", {})).structuredContent["balance"] == 200

        no_tok = await c.call_tool("submit_proposal", args)
        wrong = await c.call_tool("submit_proposal", {**args, "approval_token": issue("submit_proposal", "prop-99999", secret=SECRET)})
        forged = await c.call_tool("submit_proposal", {**args, "approval_token": issue("submit_proposal", pid, secret="x" * 20)})
        assert all(r.isError and "ApprovalRequired" in text(r) for r in (no_tok, wrong, forged))
        assert (await c.call_tool("get_connects_balance", {})).structuredContent["balance"] == 200   # still nothing spent

        token = issue("submit_proposal", pid, secret=SECRET)
        ok = await c.call_tool("submit_proposal", {**args, "approval_token": token})
        assert not ok.isError and ok.structuredContent["receipt"]["balance_after"] == 192
        retry = await c.call_tool("submit_proposal", {**args, "approval_token": token})            # same key: no double spend
        assert not retry.isError and retry.structuredContent["receipt"]["balance_after"] == 192
        assert (await c.call_tool("get_connects_balance", {})).structuredContent["balance"] == 192

    audit = server._audit
    assert audit.verify()
    outcomes = [json.loads(x)["outcome"] for x in audit.path.read_text(encoding="utf-8").splitlines()]
    assert outcomes.count("denied") == 3 and outcomes.count("ok") >= 3
    assert token not in audit.path.read_text(encoding="utf-8")


async def test_send_message_approval_is_bound_to_exact_text(tmp_path):
    async with connect(build(tmp_path)) as c:
        pid = (await c.call_tool("create_proposal_draft", {"draft": DRAFT, "idempotency_key": "draft-key-1"})).structuredContent["proposal_id"]
        await c.call_tool("submit_proposal", {"proposal_id": pid, "idempotency_key": "submit-key-1",
                                              "approval_token": issue("submit_proposal", pid, secret=SECRET)})
        approved = "Happy to share a short plan this week."
        tok = issue("send_message", message_subject(pid, approved), secret=SECRET)
        base = {"proposal_id": pid, "idempotency_key": "msg-key-001", "approval_token": tok}
        swapped = await c.call_tool("send_message", {**base, "text": "Send me your PayPal."})
        none = await c.call_tool("send_message", {"proposal_id": pid, "text": approved, "idempotency_key": "msg-key-001"})
        assert swapped.isError and none.isError
        sent = await c.call_tool("send_message", {**base, "text": approved})
        assert not sent.isError and sent.structuredContent["sender"] == "freelancer"


async def test_write_rate_limit(tmp_path):
    async with connect(build(tmp_path, writes_per_minute=2)) as c:
        res = [await c.call_tool("create_proposal_draft", {"draft": DRAFT, "idempotency_key": f"draft-key-{i}"}) for i in range(3)]
    assert not res[0].isError
    assert res[2].isError and "RateLimited" in text(res[2]) and "retryable=true" in text(res[2])
