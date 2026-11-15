"""Plumbing nodes of the conversation graph (5.4): context loading, the human approval gate for outgoing messages, the sender
(the ONLY place a client message can leave the system) and a dead letter for stopped runs.

Same contract as the bid graph (R4): nothing is sent without `message_gate` returning approved/edited, and the sender is idempotent
(key derived from proposal + exact text), so a crash/resume or retried step cannot send a message twice. A human-edited reply is
checked by the same policy guard as a drafted one.
"""
from __future__ import annotations

import hashlib
import inspect
import time
from typing import Any

from langgraph.types import interrupt

from agent.approvals import ApprovalError, message_subject
from agent.marketplace_port import MarketplaceError
from agent.policy.engine import check_draft

from .common import Deps, approval_kwargs, fail, finish


def conversation_stop_reason(state: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Why the conversation run must stop and surface as needs_attention, else None (pure, so routing is testable)."""
    b = cfg["budgets"]
    u = state.get("usage") or {}
    if state.get("steps", 0) >= b["max_graph_steps"]:
        return f"step budget exhausted ({state.get('steps')} >= {b['max_graph_steps']})"
    if u.get("tokens_in", 0) + u.get("tokens_out", 0) > b["max_tokens_per_job"]:
        return "token budget exhausted"
    if not state.get("conversation"):
        return "client_liaison produced no result: " + next((e for e in state.get("errors", []) if e.startswith("client_liaison")), "unknown error")
    return None


async def _maybe_await(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


async def load_context(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Read-only: earlier thread messages and free slots. Failures degrade (recorded), they never stop the run."""
    t0 = time.perf_counter()
    try:
        upd: dict[str, Any] = {}
        errs: list[str] = []
        pid = state.get("proposal_id")
        if deps.market is not None and pid and not state.get("thread"):
            try:
                upd["thread"] = [m.model_dump() for m in await deps.market.list_messages(pid)]
            except Exception as e:
                errs.append(f"load_context: thread unavailable ({type(e).__name__})")
        if not state.get("slots"):
            try:
                raw = await _maybe_await(deps.slots())
                upd["slots"] = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in (raw or [])][:5]
            except Exception as e:
                errs.append(f"load_context: slots unavailable ({type(e).__name__})")
        if errs:
            upd["errors"] = errs
        return finish("load_context", t0, upd, mode="read-only", degraded=bool(errs))
    except Exception as e:
        return fail("load_context", t0, e)


async def message_gate(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Human-in-the-loop for client messages. Side-effect free before `interrupt` (the node re-runs from the top on resume).

    A flagged (unsafe) message or an empty draft offers only edit/dismiss: the system never auto-answers it.
    """
    t0 = time.perf_counter()
    conv = dict(state.get("conversation") or {})
    reply = (conv.get("reply") or "").strip()
    flagged = conv.get("intent") == "unsafe"
    actions = ["approve", "edit", "dismiss"] if reply and not flagged else ["edit", "dismiss"]
    resp = interrupt({"kind": "message_approval", "proposal_id": state.get("proposal_id"), "client_message": state.get("client_message"),
                      "intent": conv.get("intent"), "draft_reply": reply, "slots": conv.get("slots", []), "flagged": flagged,
                      "reason": conv.get("reason", ""), "needs_attention": flagged or conv.get("needs_human_decision", True),
                      "actions": actions})
    resp = resp if isinstance(resp, dict) else {"status": str(resp)}
    status = resp.get("status", "dismiss")
    approval: dict[str, Any] = {"status": status, "original_text": reply}
    if status == "approve" and "approve" in actions:
        approval["final_text"] = reply
        astatus = "approved"
    elif status == "edit" and resp.get("edited_text", "").strip():
        v = check_draft(resp["edited_text"])
        if v.allowed:
            approval["final_text"] = approval["edited_text"] = v.cleaned_cover_letter or resp["edited_text"]
            astatus = "edited"
        else:
            astatus = "blocked_after_edit"
            approval["reason"] = "edit violates policy: " + ", ".join(x.rule_id for x in v.violations if x.severity == "block")
    else:
        astatus = "dismissed"
        approval["reason"] = resp.get("reason", "")
    return finish("message_gate", t0, {"approval": approval, "approval_status": astatus}, mode="human", status=astatus)


async def message_sender(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Deterministic, not an LLM node. Runs only after approval. Idempotent: same (proposal, text) => same key => one message."""
    t0 = time.perf_counter()
    try:
        text = (state.get("approval") or {}).get("final_text", "")
        pid = state.get("proposal_id", "")
        if not text or not pid:
            return finish("message_sender", t0, {"sent_message": {"status": "nothing_to_send"}}, mode="skipped")
        if deps.market is None:
            return finish("message_sender", t0, {"sent_message": {"status": "pending", "reason": "no marketplace attached to this run"}},
                          mode="skipped")
        key = hashlib.sha256(f"{pid}|{text}".encode()).hexdigest()[:32]
        try:
            msg = await deps.market.send_message(pid, text, f"msg-{key}",
                                                 **approval_kwargs(deps, "send_message", message_subject(pid, text), text))
            return finish("message_sender", t0, {"approval_status": "sent", "sent_message": {"status": "sent", "message_id": msg.message_id}},
                          mode="sent")
        except (MarketplaceError, ApprovalError) as e:  # retryable ones are queued for the worker; never swallowed silently
            retryable = bool(getattr(e, "retryable", False))
            return finish("message_sender", t0, {"approval_status": "send_failed", "needs_attention": True,
                                                 "sent_message": {"status": "retry" if retryable else "failed",
                                                                  "error": f"{type(e).__name__}: {e}"}},
                          mode="error", retryable=retryable)
    except Exception as e:
        return fail("message_sender", t0, e)


async def conv_dead_letter(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    reason = conversation_stop_reason(state, deps.cfg) or "run stopped"
    return finish("conv_dead_letter", t0, {"needs_attention": True,
                                           "dead_letter": {"reason": reason, "state_keys": sorted(state),
                                                           "errors": list(state.get("errors", []))[-5:]}},
                  mode="stopped", reason=reason)
