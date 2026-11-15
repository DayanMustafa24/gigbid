"""Workspace MCP server: calendar availability, TENTATIVE interview holds, email DRAFTS. Official MCP SDK (FastMCP).

Backed by a local MOCK (mcp_servers/workspace/store.py, config `workspace:`), documented in docs/TOOLS.md.
By design there is no tool that sends email or confirms a meeting: a human does that from the draft / tentative hold.
Run: MCP_BEARER_TOKEN=... python -m mcp_servers.workspace.server   (Streamable HTTP, default :9103/mcp, /healthz)
"""
from __future__ import annotations

import os
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from agent.settings import ROOT, get_settings
from mcp_servers.marketplace.guard import AuditLog, BearerAuthMiddleware, RateLimiter, ToolRateLimited, current_caller

from .store import EmailDraft, Hold, Slot, WorkspaceError, WorkspaceStore

IdemKey = Annotated[str, Field(min_length=8, max_length=128, description="Unique per intended action; reuse on retry.")]
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
REMOVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def create_server(store: WorkspaceStore | None = None, *, audit_path=None, per_minute: int = 60,
                  host: str = "127.0.0.1", port: int = 9103) -> FastMCP:
    store = store or WorkspaceStore()
    audit = AuditLog(audit_path or ROOT / "data" / "audit" / "workspace_audit.jsonl")
    limiter = RateLimiter(per_minute)
    mcp = FastMCP(
        "gigbid-workspace",
        instructions=(
            "Mock calendar + mailbox. Use get_free_slots to propose interview times, hold_slot to reserve one TENTATIVELY, "
            "draft_email to prepare a message. Nothing is ever sent or confirmed by this server: a human does that."
        ),
        host=host,
        port=port,
    )
    mcp._audit = audit  # exposed for tests

    def run(tool: str, args: dict, fn, *, write: bool = False):
        try:
            limiter.check(current_caller())
        except ToolRateLimited as e:
            audit.append(tool=tool, write=write, args=args, outcome="rate_limited")
            raise ToolError(f"RateLimited: {e} (retryable=true)") from e
        try:
            result = fn()
        except WorkspaceError as e:
            audit.append(tool=tool, write=write, args=args, outcome="error", error=f"{e.code}: {e}")
            raise ToolError(f"{e.code}: {e} (retryable=false)") from e
        if write:
            audit.append(tool=tool, write=True, args=args, outcome="ok")
        return result

    @mcp.tool(annotations=READ)
    def get_free_slots(
        date_from: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD, owner-local")],
        date_to: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
        duration_min: Annotated[int, Field(ge=15, le=240)] = 30,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> list[Slot]:
        """Free meeting slots (ISO-8601 with offset) within working hours, clear of existing events and holds."""
        return run("get_free_slots", {"date_from": date_from, "date_to": date_to, "duration_min": duration_min},
                   lambda: store.free_slots(date_from, date_to, duration_min, limit))

    @mcp.tool(annotations=READ)
    def list_holds() -> list[Hold]:
        """Active tentative holds."""
        return run("list_holds", {}, store.list_holds)

    @mcp.tool(annotations=WRITE)
    def hold_slot(
        start: Annotated[str, Field(description="ISO-8601 start taken from get_free_slots")],
        idempotency_key: IdemKey,
        duration_min: Annotated[int, Field(ge=15, le=240)] = 30,
        title: Annotated[str, Field(min_length=1, max_length=200)] = "Interview (tentative)",
        proposal_id: str | None = None,
    ) -> Hold:
        """Reserve a slot TENTATIVELY (never confirmed). Fails with SlotUnavailable if it is no longer free."""
        return run("hold_slot", {"start": start, "duration_min": duration_min, "title": title, "proposal_id": proposal_id},
                   lambda: store.hold_slot(start, duration_min, title, idempotency_key, proposal_id), write=True)

    @mcp.tool(annotations=REMOVE)
    def release_hold(hold_id: str) -> Hold:
        """Release a tentative hold so the slot becomes free again."""
        return run("release_hold", {"hold_id": hold_id}, lambda: store.release_hold(hold_id), write=True)

    @mcp.tool(annotations=WRITE)
    def draft_email(
        to: str,
        subject: Annotated[str, Field(min_length=1, max_length=300)],
        body: Annotated[str, Field(min_length=1, max_length=10000)],
        idempotency_key: IdemKey,
        proposal_id: str | None = None,
    ) -> EmailDraft:
        """Save an email DRAFT for the human to review and send. This server cannot send email."""
        return run("draft_email", {"to": to, "subject": subject, "proposal_id": proposal_id},   # body not audited (PII)
                   lambda: store.draft_email(to, subject, body, idempotency_key, proposal_id), write=True)

    @mcp.tool(annotations=READ)
    def list_email_drafts(proposal_id: str | None = None) -> list[EmailDraft]:
        """Saved drafts, optionally for one proposal."""
        return run("list_email_drafts", {"proposal_id": proposal_id}, lambda: store.list_drafts(proposal_id))

    return mcp


def build_app(mcp: FastMCP, token: str | None):
    return BearerAuthMiddleware(mcp.streamable_http_app(), token)


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token and os.environ.get("MCP_ALLOW_ANONYMOUS") != "1":
        raise SystemExit("MCP_BEARER_TOKEN is not set (set it, or MCP_ALLOW_ANONYMOUS=1 for local experiments only)")
    if get_settings().app_env == "production" and token in ("", "replace_me"):
        raise SystemExit("refusing to start in production with a default bearer token")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_WORKSPACE_PORT", "9103"))
    uvicorn.run(build_app(create_server(host=host, port=port), token or None), host=host, port=port)


if __name__ == "__main__":
    main()
