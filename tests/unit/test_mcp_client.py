"""Agent-side MCP gateway: allowlists, retries, circuit breaker, error parsing, config policy, in-memory integration."""
import asyncio
from datetime import UTC, datetime

import pytest

from agent.mcp_client import (
    InMemoryTransport,
    McpGateway,
    McpNotAllowed,
    McpToolError,
    McpUnavailable,
    parse_tool_error,
)
from agent.settings import load_config

ALLOW = {
    "writer": {"portfolio": ["search_evidence"]},
    "analyst": {},
    "executor": {"marketplace": ["submit_proposal"]},
}


class Fake:
    """Scripted transport: each entry is a value to return or an Exception to raise."""

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    async def call(self, server, tool, args, *, caller):
        self.calls += 1
        item = self.script.pop(0) if self.script else "ok"
        if isinstance(item, Exception):
            raise item
        return item


def gw(transport, **kw):
    sleeps: list[float] = []

    async def sleep(s):
        sleeps.append(s)
    t = [0.0]
    g = McpGateway(transport, ALLOW, sleep=sleep, clock=lambda: t[0], **kw)
    return g, sleeps, t


def test_parse_tool_error_formats():
    e = parse_tool_error("RateLimited: rate limit exceeded; retry in 5.0s (retryable=true retry_after=5.0)")
    assert (e.code, e.retryable, e.retry_after) == ("RateLimited", True, 5.0)
    e = parse_tool_error("PolicyRejected: needs disclosure (retryable=false rule=PU-ai-disclosure)")
    assert (e.code, e.retryable) == ("PolicyRejected", False)
    e = parse_tool_error("Error executing tool hold_slot: SlotUnavailable: taken (retryable=false)")   # FastMCP prefix
    assert (e.code, e.retryable) == ("SlotUnavailable", False)
    assert parse_tool_error("something odd").code == "ToolError"


async def test_allowlist_is_least_privilege():
    g, _, _ = gw(Fake([]))
    assert await g.call("writer", "portfolio", "search_evidence", {"requirement": "x"}) == "ok"
    for node, server, tool in [("writer", "marketplace", "submit_proposal"), ("writer", "portfolio", "get_profile"),
                               ("analyst", "portfolio", "search_evidence"), ("nobody", "portfolio", "search_evidence")]:
        with pytest.raises(McpNotAllowed):
            await g.call(node, server, tool)
    assert await g.call("executor", "marketplace", "submit_proposal") == "ok"


async def test_retryable_tool_errors_honour_retry_after_then_succeed():
    f = Fake([McpToolError("RateLimited", "slow", True, 1.0), McpToolError("Unavailable", "blip", True), "done"])
    g, sleeps, _ = gw(f, backoff_base_s=0.25)
    assert await g.call("writer", "portfolio", "search_evidence") == "done"
    assert sleeps == [1.0, 0.5] and f.calls == 3          # retry_after wins; else backoff 0.25 * 2**attempt


async def test_non_retryable_tool_error_is_not_retried_and_does_not_open_circuit():
    f = Fake([McpToolError("NotFound", "nope", False)])
    g, sleeps, _ = gw(f)
    with pytest.raises(McpToolError):
        await g.call("writer", "portfolio", "search_evidence")
    assert f.calls == 1 and sleeps == [] and g.health()["portfolio"]["up"]


async def test_circuit_opens_fails_fast_then_recovers():
    f = Fake([ConnectionError("refused")] * 3)
    g, sleeps, clock = gw(f, max_retries=2, cooldown_s=30)
    with pytest.raises(McpUnavailable):
        await g.call("writer", "portfolio", "search_evidence")
    assert f.calls == 3 and len(sleeps) == 2
    assert g.health()["portfolio"]["up"] is False and "ConnectionError" in g.health()["portfolio"]["last_error"]
    with pytest.raises(McpUnavailable, match="circuit open"):
        await g.call("writer", "portfolio", "search_evidence")
    assert f.calls == 3                                    # failed fast, transport untouched
    clock[0] = 31                                          # cooldown over: probe again, script now returns "ok"
    assert await g.call("writer", "portfolio", "search_evidence") == "ok"
    assert g.health()["portfolio"]["up"] is True


async def test_timeout_counts_as_unavailable():
    class Hang:
        async def call(self, *a, **k):
            await asyncio.sleep(5)

    g, _, _ = gw(Hang(), timeout_s=0.01, max_retries=0)
    with pytest.raises(McpUnavailable):
        await g.call("writer", "portfolio", "search_evidence")


def test_config_policy_no_llm_node_can_write_or_spend():
    cfg = load_config()["mcp"]
    allow = cfg["allowlists"]
    assert allow["analyst"] == {} and allow["critic"] == {}                      # tool-less nodes (injection defence)
    spend = {"submit_proposal", "send_message", "create_proposal_draft"}
    for node, per in allow.items():
        tools = {t for ts in per.values() for t in ts}
        assert (tools & spend) == (spend if node == "executor" else set()), node
    g = McpGateway.from_config(transport=Fake([]))
    assert g.allowed_tools("executor", "marketplace") and g.allowed_tools("writer", "marketplace") == []


async def test_in_memory_integration_with_real_workspace_server(tmp_path):
    pytest.importorskip("mcp.shared.memory")
    from mcp_servers.workspace.server import create_server
    from mcp_servers.workspace.store import RecurringBusy, WorkspaceConfig, WorkspaceStore

    cfg = WorkspaceConfig(utc_offset_hours=5, recurring_busy=[RecurringBusy(title="L", days=[0], start="13:00", end="14:00")])
    ws = WorkspaceStore(cfg, tmp_path / "s.json", clock=lambda: datetime(2026, 11, 16, 3, 0, tzinfo=UTC))
    server = create_server(ws, audit_path=tmp_path / "a.jsonl")
    g = McpGateway.from_config(transport=InMemoryTransport({"workspace": server}))
    slots = await g.call("client_liaison", "workspace", "get_free_slots", {"date_from": "2026-11-16"})
    assert isinstance(slots, list) and slots[0]["start"].startswith("2026-11-16T10:00")      # 08:00 clock + 2h notice
    hold = await g.call("client_liaison", "workspace", "hold_slot", {"start": slots[0]["start"], "idempotency_key": "hold-key-1"})
    assert hold["status"] == "tentative"
    with pytest.raises(McpToolError) as e:
        await g.call("client_liaison", "workspace", "hold_slot", {"start": slots[0]["start"], "idempotency_key": "hold-key-2"})
    assert e.value.code == "SlotUnavailable" and not e.value.retryable
    with pytest.raises(McpNotAllowed):
        await g.call("writer", "workspace", "hold_slot", {})
