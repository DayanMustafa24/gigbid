"""Graph plumbing nodes that are not agents: intake (read-only context), approval gate (human interrupt), executor (writes AFTER approval),
dead letter (never drop a run silently) and the single `stop_reason` used by every routing function (budgets, failed branches, policy block).

R4: the ONLY place a proposal can reach the marketplace is `executor`, and the ONLY way to reach it is through `approval_gate`
returning approved/edited. The executor uses idempotency keys derived from (job, final text), so a crash-and-resume or a retried
step can never create or submit a proposal twice.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from langgraph.types import interrupt

from agent.approvals import ApprovalError
from agent.marketplace_port import MarketplaceError, ProposalIn
from agent.policy.engine import check_draft

from .common import Deps, approval_kwargs, fail, finish


def stop_reason(state: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Why the run must stop and surface as needs_attention, else None. Pure, so routing is testable."""
    b = cfg["budgets"]
    u = state.get("usage") or {}
    if state.get("steps", 0) >= b["max_graph_steps"]:
        return f"step budget exhausted ({state.get('steps')} >= {b['max_graph_steps']})"
    if u.get("tokens_in", 0) + u.get("tokens_out", 0) > b["max_tokens_per_job"]:
        return "token budget exhausted"
    for key, node in (("risk_report", "risk_assessor"), ("fit_report", "fit_matcher"), ("job_spec", "analyst")):
        if not state.get(key):
            return f"{node} produced no result: {next((e for e in state.get('errors', []) if e.startswith(node)), 'unknown error')}"
    v = state.get("compliance_verdict")
    if v is not None and not v.get("allowed", True):
        return "compliance blocked the draft: " + ", ".join(x["rule_id"] for x in v.get("violations", []) if x.get("severity") == "block")
    return None


async def intake(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Read-only context: client history and platform policy. A failure degrades (recorded), it does not stop the run."""
    t0 = time.perf_counter()
    try:
        job, upd, errs = state["job"], {"job_id": state["job"]["job_id"]}, []
        if deps.persona and not state.get("persona"):
            upd["persona"] = deps.persona
        if deps.market is not None:
            if not state.get("client_history") and job.get("client_id"):
                try:
                    upd["client_history"] = (await deps.market.get_client_history(job["client_id"])).model_dump()
                except Exception as e:
                    errs.append(f"intake: client history unavailable ({type(e).__name__})")
            if not state.get("platform_policy"):
                try:
                    upd["platform_policy"] = (await deps.market.get_platform_policy()).model_dump()
                except Exception as e:
                    errs.append(f"intake: platform policy unavailable ({type(e).__name__})")
        if deps.memory is not None and not state.get("memory_context"):
            try:
                upd["memory_context"] = deps.memory.context(deps.owner, upd.get("persona") or state.get("persona") or deps.persona, deps.cfg)
            except Exception as e:  # memory is an optimisation: a broken store degrades the run, it never stops it
                errs.append(f"intake: memory unavailable ({type(e).__name__})")
        if errs:
            upd["errors"] = errs
        return finish("intake", t0, upd, mode="read-only", degraded=bool(errs))
    except Exception as e:
        return fail("intake", t0, e)


async def join(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Barrier after the parallel Analyst/Risk/Fit branches. Does no work; it exists so the graph waits for all three."""
    return finish("join", time.perf_counter(), {}, mode="barrier")


async def approval_gate(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Human-in-the-loop (R4). Everything before `interrupt` is side-effect free because the node re-runs from the top on resume."""
    t0 = time.perf_counter()
    draft = dict(state["proposal_draft"])
    crit = state.get("critique") or {}
    resp = interrupt({
        "kind": "proposal_approval", "job_id": state.get("job_id"), "draft": draft, "price_quote": state.get("price_quote"),
        "critique": crit, "compliance": state.get("compliance_verdict"), "verification": state.get("verification_report"),
        "submission_flags": state.get("submission_flags"), "needs_attention": not crit.get("passed", True),
        "actions": ["approve", "edit", "reject"],
    })
    resp = resp if isinstance(resp, dict) else {"status": str(resp)}
    status = resp.get("status", "reject")
    approval: dict[str, Any] = {"status": status, "original_text": draft["cover_letter"]}
    upd: dict[str, Any] = {}
    if status == "edit" and resp.get("edited_text", "").strip():
        v = check_draft(resp["edited_text"], list(draft.get("screening_answers", [])))  # a human edit is checked by the same guard
        if v.allowed:
            draft["cover_letter"] = v.cleaned_cover_letter or resp["edited_text"]
            approval.update(edited_text=draft["cover_letter"])
            upd["proposal_draft"] = draft
            upd["approval_status"] = "edited"
        else:
            upd["approval_status"] = "blocked_after_edit"
            approval["reason"] = "edit violates policy: " + ", ".join(x.rule_id for x in v.violations if x.severity == "block")
    elif status == "approve":
        upd["approval_status"] = "approved"
    else:
        upd["approval_status"] = "rejected"
        approval["reason"] = resp.get("reason", "")
    upd["approval"] = approval
    return finish("approval_gate", t0, upd, mode="human", status=upd["approval_status"])


async def executor(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Deterministic, not an LLM node. Runs only after approval. Idempotent: same (job, text) => same keys => no double spend."""
    t0 = time.perf_counter()
    try:
        d, q = state["proposal_draft"], state.get("price_quote") or {}
        if (state.get("job") or {}).get("manual"):  # a pasted job is not on the marketplace: the human copies the approved text
            return finish("executor", t0, {"submission": {"status": "pending", "reason": "manual job (not on the marketplace): copy the approved proposal"}},
                          mode="skipped")
        if deps.market is None:
            return finish("executor", t0, {"submission": {"status": "pending", "reason": "no marketplace attached to this run"}},
                          mode="skipped")
        key = hashlib.sha256(f"{state['job_id']}|{d['cover_letter']}|{d.get('screening_answers')}".encode()).hexdigest()[:32]
        try:
            prop = await deps.market.create_proposal_draft(
                ProposalIn(job_id=state["job_id"], cover_letter=d["cover_letter"], screening_answers=list(d.get("screening_answers", [])),
                           bid_amount=float(q.get("amount", 0)), bid_type=q.get("pricing_type", "fixed"),
                           ai_disclosure=bool((state.get("submission_flags") or {}).get("ai_disclosure", False))),
                f"draft-{key}")
            prev = await deps.market.preview_submission(prop.proposal_id)
            if not prev.submittable:
                return finish("executor", t0, {"approval_status": "submission_blocked", "needs_attention": True,
                                               "submission": {"status": "blocked", "proposal_id": prop.proposal_id,
                                                              "issues": [i.model_dump() for i in prev.issues]}}, mode="preview")
            rec = await deps.market.submit_proposal(prop.proposal_id, f"submit-{key}",
                                                    **approval_kwargs(deps, "submit_proposal", prop.proposal_id, d["cover_letter"]))
            return finish("executor", t0, {"approval_status": "submitted",
                                           "submission": {"status": rec.status, "proposal_id": rec.proposal_id,
                                                          "connects_spent": rec.connects_spent, "balance_after": rec.balance_after}},
                          mode="submitted")
        except (MarketplaceError, ApprovalError) as e:  # retryable ones are queued for the worker; never swallowed silently
            retryable = bool(getattr(e, "retryable", False))
            return finish("executor", t0, {"approval_status": "submission_failed", "needs_attention": True,
                                           "submission": {"status": "retry" if retryable else "failed", "error": f"{type(e).__name__}: {e}"}},
                          mode="error", retryable=retryable)
    except Exception as e:
        return fail("executor", t0, e)


async def dead_letter(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    reason = stop_reason(state, deps.cfg) or "run stopped"
    return finish("dead_letter", t0, {"needs_attention": True, "dead_letter": {"reason": reason, "state_keys": sorted(state),
                                                                                "errors": list(state.get("errors", []))[-5:]}},
                  mode="stopped", reason=reason)


async def not_worth(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Pricing says the Connects cost outweighs the expected value: downgrade a `bid` to `skip` with a visible reason."""
    t0 = time.perf_counter()
    q = state.get("price_quote") or {}
    det = dict(state.get("decision_detail") or {})
    det.update(action="skip", reasons=[*det.get("reasons", []), f"Connects not worth it: {q.get('rationale', '')}"])
    return finish("not_worth", t0, {"decision": "skip", "decision_detail": det}, mode="deterministic")
