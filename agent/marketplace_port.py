"""The ONE marketplace interface (R1). Graph/agents/MCP server import only this module.

Two adapters implement it (selected by config `marketplace.mode`): GigSim (mcp_servers/marketplace/adapters/gigsim.py)
and live Upwork (adapters/upwork_live.py, Phase 3.5). Everything here is typed; adapters translate their own wire
format and errors into these models and exceptions.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent.settings import get_settings, load_config, marketplace_mode

# ---- errors (adapters map transport errors onto these) ---------------------------------------------------------


class MarketplaceError(Exception):
    """Base class. `retryable` tells callers (worker/graph) whether trying again later can help."""
    retryable = False


class Unavailable(MarketplaceError):
    retryable = True


class RateLimited(MarketplaceError):
    retryable = True

    def __init__(self, message: str, retry_after: float = 1.0):
        super().__init__(message)
        self.retry_after = retry_after


class NotFound(MarketplaceError): ...


class InvalidRequest(MarketplaceError): ...


class Conflict(MarketplaceError): ...


class AuthError(MarketplaceError): ...


class InsufficientConnects(MarketplaceError): ...


class PolicyRejected(MarketplaceError):
    def __init__(self, message: str, rule: str | None = None):
        super().__init__(message)
        self.rule = rule


# ---- models ------------------------------------------------------------------------------------------------------


class Budget(BaseModel):
    min: float
    max: float
    currency: str = "USD"
    type: Literal["fixed", "hourly"]


class JobSummary(BaseModel):
    job_id: str
    title: str
    snippet: str = ""
    budget: Budget
    required_skills: list[str] = Field(default_factory=list)
    connects_cost: int
    client_id: str


class Job(JobSummary):
    description: str
    screening_questions: list[str] = Field(default_factory=list)
    duration_days: int | None = None


class ClientHistory(BaseModel):
    client_id: str
    payment_verified: bool
    completed_projects: int
    average_rating: float | None = None
    total_spent: float = 0
    member_since_days: int = 0


class ConnectsBalance(BaseModel):
    balance: int


class PlatformPolicy(BaseModel):
    """Platform rules/costs the agent must respect. Read at runtime so a policy update needs no code change."""
    version: str
    update_active: bool = False
    connects_surcharge: int = 0
    requires_ai_disclosure: bool = False
    effective: str | None = None
    notes: list[str] = Field(default_factory=list)


class ProposalIn(BaseModel):
    job_id: str
    cover_letter: str = Field(min_length=1, max_length=5000)
    screening_answers: list[str] = Field(default_factory=list, max_length=20)
    bid_amount: float = Field(ge=0)
    bid_type: Literal["fixed", "hourly"] = "fixed"
    ai_disclosure: bool = False


class Proposal(BaseModel):
    proposal_id: str
    job_id: str
    status: str                  # draft | submitted | replied | interview | hired | closed | ignored
    connects_cost: int = 0
    connects_spent: int = 0
    bid_amount: float = 0
    bid_type: str = "fixed"
    ai_disclosure: bool = False


class SubmissionIssue(BaseModel):
    code: str
    message: str
    rule: str | None = None


class SubmissionPreview(BaseModel):
    """Result of a dry run: what a submit WOULD do. Nothing is spent or sent."""
    proposal_id: str
    would_spend: int
    balance_before: int
    balance_after: int
    submittable: bool
    issues: list[SubmissionIssue] = Field(default_factory=list)


class SubmissionReceipt(BaseModel):
    proposal_id: str
    status: str
    connects_spent: int
    balance_after: int


class Message(BaseModel):
    message_id: str
    proposal_id: str
    sender: Literal["freelancer", "client"]
    text: str
    sent_at: str


# ---- the port ------------------------------------------------------------------------------------------------------


@runtime_checkable
class MarketplacePort(Protocol):
    """Reads are safe to retry. Every write takes an idempotency key; a retry with the same key never repeats it."""

    # reads
    async def search_jobs(self, query: str, limit: int = 20, offset: int = 0) -> list[JobSummary]: ...
    async def get_job(self, job_id: str) -> Job: ...
    async def get_client_history(self, client_id: str) -> ClientHistory: ...
    async def get_connects_balance(self) -> ConnectsBalance: ...
    async def get_platform_policy(self) -> PlatformPolicy: ...
    async def get_proposal(self, proposal_id: str) -> Proposal: ...
    async def list_messages(self, proposal_id: str) -> list[Message]: ...
    # writes (callers must hold a human approval before submit_proposal / send_message: enforced above this layer)
    async def create_proposal_draft(self, draft: ProposalIn, idempotency_key: str) -> Proposal: ...
    async def preview_submission(self, proposal_id: str) -> SubmissionPreview: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> SubmissionReceipt: ...
    async def send_message(self, proposal_id: str, text: str, idempotency_key: str) -> Message: ...
    async def aclose(self) -> None: ...


def get_marketplace(mode: str | None = None) -> MarketplacePort:
    """Config switch (R1): marketplace.mode = gigsim | upwork. Callers never import an adapter directly."""
    mode = mode or marketplace_mode()
    if mode == "gigsim":
        from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter  # lazy: adapter imports this module

        s = get_settings()
        limit = load_config()["marketplace"].get("max_requests_per_minute")
        return GigSimAdapter(s.gigsim_base_url, s.gigsim_api_token or None, max_requests_per_minute=limit)
    if mode == "upwork":
        raise NotImplementedError("live Upwork adapter arrives in Phase 3.5 (official Upwork MCP, OAuth 2.1)")
    raise ValueError(f"unknown marketplace mode {mode!r}; expected 'gigsim' or 'upwork'")
