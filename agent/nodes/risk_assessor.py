"""Risk Assessor: deterministic, data-driven (rules in agent/policy/rules.yaml, weights in config `risk:`). No LLM, no tools.

Order matters for injection safety: sentences that match a prompt-injection rule are REMOVED before the scam/off-platform rules
run, so an attacker cannot make a good job look like a scam (or the reverse) by planting text; the injection itself is flagged
(`injection_detected`) and ignored. Duplicates are detected by description similarity against already-seen jobs.
"""
from __future__ import annotations

import re
import time
from typing import Any

from agent.handoffs import RiskFlag, RiskReport
from agent.policy.engine import load_policy, matching_rules

from .common import Deps, fail, finish, jaccard, job_text

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def split_injection(text: str) -> tuple[str, list[str]]:
    """(text without injected sentences, the injected sentences). Uses the policy's prompt-injection rules."""
    pol = load_policy()
    kept, injected = [], []
    for s in _SENT.split(text):
        if not s.strip():
            continue
        if any(r.flag == RiskFlag.PROMPT_INJECTION.value for r in matching_rules(s, "job_post", pol)):
            injected.append(s.strip())
        else:
            kept.append(s)
    return " ".join(kept), injected


def is_low_budget(job: dict[str, Any], profile: dict[str, Any], cfg: dict[str, Any]) -> bool:
    b = job.get("budget") or {}
    if b.get("max") is None:
        return False
    if b.get("type") == "hourly":
        rate = profile.get("hourly_rate_usd")
        return bool(rate) and b["max"] < rate * cfg["risk"]["low_budget_hourly_factor"]
    floor = profile.get("minimum_project_budget_usd")
    return bool(floor) and b["max"] < floor


def find_duplicate(job: dict[str, Any], known: list[dict[str, Any]], threshold: float) -> str | None:
    """job_id of an already-seen near-duplicate, else None. Same client+skills is not enough: text must be similar."""
    me = job.get("description", "")
    for other in known:
        if other.get("job_id") != job.get("job_id") and jaccard(me, other.get("description", "")) >= threshold:
            return other["job_id"]
    return None


async def risk_assessor(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job, cfg = state["job"], deps.cfg
        rc = cfg["risk"]
        clean, injected = split_injection(job_text(job))
        flags: list[str] = []
        why: list[str] = []
        for r in matching_rules(clean, "job_post"):
            if r.flag and r.flag != RiskFlag.PROMPT_INJECTION.value and r.flag not in flags:
                flags.append(r.flag)
                why.append(f"{r.id}: {r.description}")
        guard = state.get("injection") or {}  # injection_guard node (heuristics/classifier): catches what the regex rules cannot
        if injected:
            flags.append(RiskFlag.PROMPT_INJECTION.value)
            why.append(f"{len(injected)} embedded instruction(s) detected and ignored")
        elif guard.get("level") == "high":
            flags.append(RiskFlag.PROMPT_INJECTION.value)
            why.append("injection guard: " + ", ".join(guard.get("reasons", [])[:3]))
        if is_low_budget(job, deps.profile, cfg):
            flags.append(RiskFlag.LOW_BUDGET.value)
            why.append("budget below the freelancer's floor")
        known = deps.known_jobs()  # ALREADY-SEEN jobs only (caller's contract), so an original is never a duplicate of its repost
        dup = find_duplicate(job, known, rc["duplicate_similarity"]) if known else None
        if dup:
            flags.append(RiskFlag.DUPLICATE.value)
            why.append(f"near-duplicate of {dup}")
        client = state.get("client_history") or {}
        client_score = None
        if client:
            cs = 1.0
            if not client.get("payment_verified", True):
                flags.append(RiskFlag.UNVERIFIED_PAYMENT.value)
                cs -= 0.4
            rating = client.get("average_rating")
            if (rating is not None and rating < rc["client"]["min_rating"]) or (
                    client.get("completed_projects", 1) == 0 and client.get("member_since_days", 999) < rc["client"]["young_account_days"]):
                flags.append(RiskFlag.POOR_CLIENT_HISTORY.value)
                cs -= 0.4
            client_score = max(0.0, cs)
        score = min(1.0, sum(rc["weights"].get(f, 0.0) for f in dict.fromkeys(flags)))
        report = RiskReport(score=round(score, 3), flags=list(dict.fromkeys(flags)), explanation="; ".join(why),
                            client_score=client_score, injection_detected=bool(injected) or guard.get("level") == "high")
        return finish("risk_assessor", t0, {"risk_report": report.model_dump()}, mode="deterministic", flags=report.flags)
    except Exception as e:
        return fail("risk_assessor", t0, e)
