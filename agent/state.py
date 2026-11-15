"""Typed graph state (assignment C1). Starter-kit keys are kept; additions only.

Reducers matter because Job Analyst / Risk / Fit run in PARALLEL (Send). Each branch writes its OWN key, so no write conflicts;
the keys that several nodes append to (errors, events, usage) use additive reducers.

Three separations (PLAN section 5): this State is per job; `events` is the execution trace (audit); long-term memory lives in
agent/memory and is only read into `memory_context`, never written from here.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow merge, right wins. Used for usage counters and per-branch metadata."""
    return {**(left or {}), **(right or {})}


def add_usage(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, float]:
    """Sum numeric counters key by key: {'tokens_in': .., 'tokens_out': .., 'llm_calls': .., 'cost_usd': ..}."""
    out = dict(left or {})
    for k, v in (right or {}).items():
        out[k] = out.get(k, 0) + v
    return out


class GigBidState(TypedDict, total=False):
    # ---- starter-kit keys (unchanged) ----------------------------------------------------------------------------
    job: dict[str, Any]
    job_spec: dict[str, Any]
    risk_report: dict[str, Any]
    fit_report: dict[str, Any]
    decision: str
    evidence: list[dict[str, Any]]
    price_quote: dict[str, Any]
    proposal_draft: dict[str, Any]
    claim_verification: list[dict[str, Any]]
    critique: dict[str, Any]
    compliance_verdict: dict[str, Any]
    approval_status: str
    errors: Annotated[list[str], operator.add]
    # ---- additions -----------------------------------------------------------------------------------------------
    job_id: str
    persona: str
    trace_id: str
    decision_detail: dict[str, Any]  # BidDecision as dict (action, reasons, confidence)
    memory_context: dict[str, Any]  # read-only view of promoted style/outcome memory for this run
    rag_meta: dict[str, Any]  # variant, abstained, unsupported_requirements, latency (from rag.Retrieval.meta)
    verification_report: dict[str, Any]  # before/after unsupported rates from rag.verify
    revision: int  # critic loop counter, hard-capped by budgets.max_revisions
    steps: Annotated[int, operator.add]  # nodes executed, checked against budgets.max_graph_steps
    usage: Annotated[dict[str, float], add_usage]  # tokens / calls / cost accumulated across nodes
    events: Annotated[list[dict[str, Any]], operator.add]  # execution trace: one dict per node run
    needs_attention: bool  # budget exhausted / repeated failure: surfaced in the UI, never silently dropped
    dead_letter: dict[str, Any]  # why the run stopped (node, error, last state keys)
    approval: dict[str, Any]  # human decision from interrupt(): {status, edited_text?, reason?}
    client_history: dict[str, Any]  # ClientHistory as dict (read by Risk)
    platform_policy: dict[str, Any]  # PlatformPolicy as dict (surcharge, AI disclosure): read by Pricing + Compliance
    submission_flags: dict[str, Any]  # e.g. {'ai_disclosure': True}: applied by the executor at submit time
    candidates: list[dict[str, Any]]  # Scout output: ranked job summaries
    submission: dict[str, Any]  # Executor result: proposal id, connects spent, or why nothing was submitted
    injection: dict[str, Any]  # injection guard result: {score, level, reasons, removed_chars}; read by Risk
    queries: list[str]  # Scout input: saved search queries
    limit: int  # Scout input: max results per query
    # ---- conversation graph (5.4) ----
    proposal_id: str
    client_message: str  # latest inbound client message (UNTRUSTED)
    thread: list[dict[str, Any]]  # earlier messages of this proposal thread
    slots: list[dict[str, Any]]  # free calendar slots offered by the Workspace server
    conversation: dict[str, Any]  # classified intent + reply draft + flags (ConversationDraft as dict)
    sent_message: dict[str, Any]  # Message sender result: message id, or why nothing was sent
