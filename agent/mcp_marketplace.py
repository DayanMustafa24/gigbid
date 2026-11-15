"""The marketplace as the agents see it in production: `MarketplacePort` implemented over the MCP gateway (agent/mcp_client.py).

Graph nodes, the API and the worker never touch GigSim or Upwork. Each port method becomes one MCP tool call made AS a named node, so the
per-node allowlist in `agent/config.yaml` (mcp.allowlists) decides what it may do: reads as the node that needs them, writes only as the
deterministic `executor` and only with a human approval token that the Marketplace MCP server verifies (agent/approvals.py).

Choose the path with config `marketplace.access` (env MARKETPLACE_ACCESS): `mcp` (default, the product path) or `direct` (the port's own
adapter in-process: quick local experiments and the MCP server itself, which sits in front of that adapter). Same port either way (R1).
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from agent.marketplace_port import (
    ApprovalRequired,
    AuthError,
    ClientHistory,
    Conflict,
    ConnectsBalance,
    InsufficientConnects,
    InvalidRequest,
    Job,
    JobSummary,
    MarketplaceError,
    MarketplacePort,
    Message,
    NotFound,
    PlatformPolicy,
    PolicyRejected,
    Proposal,
    ProposalIn,
    RateLimited,
    SubmissionPreview,
    SubmissionReceipt,
    Unavailable,
    get_marketplace,
)
from agent.mcp_client import McpGateway, McpToolError, McpUnavailable
from agent.settings import load_config

SERVER = "marketplace"
# port method -> the graph node whose allowlist it is checked against (least privilege; see config mcp.allowlists)
CALLER: dict[str, str] = {
    "search_jobs": "scout", "get_job": "scout",
    "get_client_history": "risk_assessor", "get_platform_policy": "compliance", "get_connects_balance": "pricing",
    "get_proposal": "client_liaison", "list_messages": "client_liaison",
    "create_proposal_draft": "executor", "preview_submission": "executor", "submit_proposal": "executor", "send_message": "executor",
}
_ERRORS: dict[str, type[MarketplaceError]] = {
    "NotFound": NotFound, "Conflict": Conflict, "InvalidRequest": InvalidRequest, "AuthError": AuthError,
    "InsufficientConnects": InsufficientConnects, "Unavailable": Unavailable, "ApprovalRequired": ApprovalRequired,
}


def to_port_error(e: McpToolError) -> MarketplaceError:
    """Tool error (stable code + retryable flag, see the server's `_tool_error`) -> the port's exception types."""
    msg = str(e)[len(e.code) + 2:] if str(e).startswith(e.code + ": ") else str(e)
    if e.code == "RateLimited":
        return RateLimited(msg, e.retry_after or 1.0)
    if e.code == "PolicyRejected":
        return PolicyRejected(msg, e.rule)
    cls = _ERRORS.get(e.code)
    if cls is not None:
        return cls(msg)
    return Unavailable(msg) if e.retryable else MarketplaceError(msg)


class McpMarketplace:
    """Implements `MarketplacePort`. Errors surface as the port's exceptions, so callers (graph nodes, API, worker) are unchanged."""

    def __init__(self, gateway: McpGateway, callers: dict[str, str] | None = None) -> None:
        self.gateway, self.callers = gateway, {**CALLER, **(callers or {})}

    async def _call(self, method: str, tool: str, args: dict[str, Any]) -> Any:
        try:
            return await self.gateway.call(self.callers[method], SERVER, tool, args)
        except McpToolError as e:
            raise to_port_error(e) from e
        except McpUnavailable as e:  # server down / circuit open: the API shows the degraded-mode banner, the worker retries later
            raise Unavailable(str(e)) from e

    def health(self) -> dict[str, dict[str, Any]]:
        return self.gateway.health()

    async def aclose(self) -> None:
        return None  # sessions are per call; nothing is held open

    # ---- reads ----------------------------------------------------------------------------------------------------------------
    async def search_jobs(self, query: str, limit: int = 20, offset: int = 0) -> list[JobSummary]:
        res = await self._call("search_jobs", "search_jobs", {"query": query, "limit": limit, "offset": offset})
        return [JobSummary.model_validate(x) for x in res or []]

    async def get_job(self, job_id: str) -> Job:
        return Job.model_validate(await self._call("get_job", "get_job", {"job_id": job_id}))

    async def get_client_history(self, client_id: str) -> ClientHistory:
        return ClientHistory.model_validate(await self._call("get_client_history", "get_client_history", {"client_id": client_id}))

    async def get_connects_balance(self) -> ConnectsBalance:
        return ConnectsBalance.model_validate(await self._call("get_connects_balance", "get_connects_balance", {}))

    async def get_platform_policy(self) -> PlatformPolicy:
        return PlatformPolicy.model_validate(await self._call("get_platform_policy", "get_platform_policy", {}))

    async def get_proposal(self, proposal_id: str) -> Proposal:
        return Proposal.model_validate(await self._call("get_proposal", "get_proposal", {"proposal_id": proposal_id}))

    async def list_messages(self, proposal_id: str) -> list[Message]:
        res = await self._call("list_messages", "list_messages", {"proposal_id": proposal_id})
        return [Message.model_validate(x) for x in res or []]

    # ---- writes (the server enforces idempotency, rate limits, the audit log and, for spending writes, the approval token) ----------
    async def create_proposal_draft(self, draft: ProposalIn, idempotency_key: str) -> Proposal:
        res = await self._call("create_proposal_draft", "create_proposal_draft",
                               {"draft": draft.model_dump(), "idempotency_key": idempotency_key})
        return Proposal.model_validate(res)

    async def preview_submission(self, proposal_id: str) -> SubmissionPreview:
        key = "preview-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:16]  # required by the tool schema; a preview has no effect
        res = await self._call("preview_submission", "submit_proposal", {"proposal_id": proposal_id, "idempotency_key": key, "dry_run": True})
        return SubmissionPreview.model_validate(res["preview"])

    async def submit_proposal(self, proposal_id: str, idempotency_key: str, approval_token: str | None = None) -> SubmissionReceipt:
        args: dict[str, Any] = {"proposal_id": proposal_id, "idempotency_key": idempotency_key, "dry_run": False}
        if approval_token:
            args["approval_token"] = approval_token
        res = await self._call("submit_proposal", "submit_proposal", args)
        return SubmissionReceipt.model_validate(res["receipt"])

    async def send_message(self, proposal_id: str, text: str, idempotency_key: str, approval_token: str | None = None) -> Message:
        args: dict[str, Any] = {"proposal_id": proposal_id, "text": text, "idempotency_key": idempotency_key}
        if approval_token:
            args["approval_token"] = approval_token
        return Message.model_validate(await self._call("send_message", "send_message", args))


def access_mode(cfg: dict[str, Any] | None = None) -> str:
    return (os.environ.get("MARKETPLACE_ACCESS") or (cfg or load_config())["marketplace"].get("access", "mcp")).lower()


def build_agent_marketplace(cfg: dict[str, Any] | None = None, gateway: McpGateway | None = None) -> MarketplacePort:
    """What the API/worker/graph use. `mcp` (default) goes through the gateway; `direct` builds the port's adapter in-process.
    (The Marketplace MCP server itself always uses `get_marketplace()`: it IS the thing in front of the adapter.)"""
    cfg = cfg or load_config()
    mode = access_mode(cfg)
    if mode == "direct":
        return get_marketplace()
    if mode != "mcp":
        raise ValueError(f"marketplace.access must be 'mcp' or 'direct', got {mode!r}")
    return McpMarketplace(gateway or McpGateway.from_config(cfg=cfg))


def workspace_slots_provider(gateway: McpGateway, *, days: int = 7, duration_min: int = 30, limit: int = 5) -> Callable[[], Awaitable[Any]]:
    """Free calendar slots through the Workspace MCP server (as `client_liaison`, the only node allowed to see them)."""
    async def provider() -> Any:
        today = date.today()
        return await gateway.call("client_liaison", "workspace", "get_free_slots", {
            "date_from": (today + timedelta(days=1)).isoformat(), "date_to": (today + timedelta(days=days)).isoformat(),
            "duration_min": duration_min, "limit": limit})
    return provider
