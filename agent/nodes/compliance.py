"""Compliance Guard: deterministic policy pass (agent/policy/rules.yaml) on the final draft, before the human sees it.

Redacts contact details, blocks guarantees and similar. The platform policy read at runtime (PlatformPolicy in state) can require an
AI-disclosure flag: it is recorded in `submission_flags` and applied by the executor at submit time, so the change request needs no
node code. No LLM here on purpose: a guard that can be argued with is not a guard.
"""
from __future__ import annotations

import time
from typing import Any

from agent.policy.engine import check_draft

from .common import Deps, fail, finish


async def compliance(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        draft = dict(state["proposal_draft"])
        verdict = check_draft(draft["cover_letter"], list(draft.get("screening_answers", [])))
        pol = state.get("platform_policy") or {}
        flags = {"ai_disclosure": bool(pol.get("requires_ai_disclosure", False))}
        upd: dict[str, Any] = {"compliance_verdict": verdict.model_dump(), "submission_flags": flags}
        if verdict.cleaned_cover_letter is not None:
            draft["cover_letter"] = verdict.cleaned_cover_letter
            draft["screening_answers"] = verdict.cleaned_screening_answers or draft.get("screening_answers", [])
            upd["proposal_draft"] = draft
        return finish("compliance", t0, upd, mode="deterministic", allowed=verdict.allowed,
                      violations=[v.rule_id for v in verdict.violations], policy_version=verdict.policy_version)
    except Exception as e:
        return fail("compliance", t0, e)
