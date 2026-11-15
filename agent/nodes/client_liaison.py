"""Client Liaison: classifies an inbound client message and drafts a reply (and optional interview slots) for human approval.

Safety first and deterministic: the message is scanned with the policy rules (prompt injection, off-platform contact/payment) BEFORE
any LLM sees it; a flagged message is classified `unsafe`, needs a human decision and gets no auto-drafted reply. The LLM (prompt
agent/prompts/liaison.md, message quoted as untrusted data) only classifies and drafts. Every reply is passed through the policy
guard (contact details redacted, guarantees blocked). Nothing is sent here: the conversation graph interrupts for approval.
"""
from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel

from agent.policy.engine import check_draft, matching_rules
from agent.security.detector import detect
from agent.security.quarantine import quote_untrusted

from .common import Deps, fail, finish

Intent = Literal["interview_request", "question", "negotiation", "scope_change", "info_request", "acceptance", "rejection", "smalltalk", "unsafe"]
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("interview_request", re.compile(r"\b(interview|call|meet(?:ing)?|available|schedule|zoom|chat live)\b", re.I)),
    ("negotiation", re.compile(r"\b(price|rate|budget|discount|cheaper|lower|negotiat)", re.I)),
    ("scope_change", re.compile(r"\b(also add|additional|extra feature|change the scope|new requirement)", re.I)),
    ("acceptance", re.compile(r"\b(you're hired|you are hired|let's start|offer you|accept)", re.I)),
    ("rejection", re.compile(r"\b(went with|not moving forward|no longer|chose another|decided against)", re.I)),
]


# Offline fallback replies (no LLM): only for intents where a neutral, promise-free note is always safe. Anything else is left
# empty so the human writes it; the guard and the approval gate still apply to every one of them.
TEMPLATES: dict[str, str] = {
    "interview_request": "Thank you for getting back to me. I'd be glad to talk. Here are some times that work for me; please pick one.",
    "acceptance": "Thank you, I'm glad to get started. Could you confirm the scope and the start date so we are aligned?",
    "rejection": "Thank you for letting me know. I appreciate your time and wish you every success with the project.",
}


class ConversationDraft(BaseModel):
    intent: Intent = "question"
    reply: str = ""
    propose_slots: bool = False
    needs_human_decision: bool = True
    reason: str = ""


def rule_intent(msg: str) -> Intent:
    for name, pat in _RULES:
        if pat.search(msg):
            return name  # type: ignore[return-value]
    return "question" if "?" in msg else "smalltalk"


def unsafe_reasons(msg: str, icfg: dict[str, Any] | None = None) -> list[str]:
    """Policy regex rules on the NORMALISED message, plus the detector's heuristics (a `high` score is treated as unsafe)."""
    det = detect(msg, icfg)
    out = [f"{r.id}: {r.description}" for r in matching_rules(det.normalized_text, "client_message")]
    if det.level == "high":
        out.append("injection heuristics: " + ", ".join(det.reasons[:3]))
    return out


async def client_liaison(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        msg = state.get("client_message", "")
        slots = state.get("slots") or []
        bad = unsafe_reasons(msg, (deps.cfg.get("security") or {}).get("injection"))
        if bad:
            conv = ConversationDraft(intent="unsafe", reply="", needs_human_decision=True, reason="; ".join(bad)[:300])
            return finish("client_liaison", t0, {"conversation": {**conv.model_dump(), "slots": []}}, mode="guard", flagged=len(bad))
        results, mode = [], "rules"
        intent0 = rule_intent(msg)
        conv = ConversationDraft(intent=intent0, reply=TEMPLATES.get(intent0, ""), propose_slots=intent0 == "interview_request" and bool(slots),
                                 needs_human_decision=True, reason="no LLM: classified by keyword rules, template reply")
        if deps.llm is not None:
            thread = "\n".join(f"{m.get('sender', '?')}: {m.get('text', '')}" for m in (state.get("thread") or [])[-6:])
            user = "\n".join([quote_untrusted("client_message", msg),
                              f"<thread>\n{thread}\n</thread>",
                              f"<profile>name={deps.profile.get('display_name')}; rate={deps.profile.get('hourly_rate_usd')}</profile>",
                              f"<slots>{'; '.join(str(s.get('start', s)) for s in slots[:5])}</slots>"])
            try:
                conv, res = await deps.llm.complete_json("client_liaison", "liaison", user, ConversationDraft, max_tokens=400)
                results.append(res)
                mode = "llm"
            except Exception as e:
                mode = f"rules(llm_error={type(e).__name__})"
        if conv.reply:
            v = check_draft(conv.reply)
            conv = conv.model_copy(update={"reply": v.cleaned_cover_letter or conv.reply,
                                           "needs_human_decision": conv.needs_human_decision or not v.allowed})
        offer = slots[:3] if conv.propose_slots and conv.intent == "interview_request" else []
        return finish("client_liaison", t0, {"conversation": {**conv.model_dump(), "slots": offer}}, results=results, mode=mode,
                      intent=conv.intent)
    except Exception as e:
        return fail("client_liaison", t0, e)
