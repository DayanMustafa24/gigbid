"""Claim verifier (R5): splits the draft into claims, labels each against portfolio evidence, removes/rewrites what is not backed.

Runs BEFORE the Critic so the Critic judges the verified text. Deterministic core (rag.verify); the optional LLM judge only moves
PARTLY supported claims. Evidence = what the Fit Matcher retrieved plus the other primary items of the same projects (a claim about a
retrieved project is checked against that whole project). Reports unsupported claims before and after (measured, per run).
Note: `unsupported_after` is a re-check by the same verifier (zero by construction); the independent number is in
evals/results/rag_verify_eval.json (planted fabrications that survive).
"""
from __future__ import annotations

import time
from typing import Any

from rag.types import PRIMARY_TYPES
from rag.verify import verify_and_repair

from .common import Deps, fail, finish

PLACEHOLDER = "[needs your answer]"


def evidence_pool(state: dict[str, Any], deps: Deps) -> list[dict[str, Any]]:
    pool = {e["evidence_id"]: {"evidence_id": e["evidence_id"], "text": e["text"]} for e in state.get("evidence") or []}
    if deps.corpus is not None:
        for e in state.get("evidence") or []:
            for it in deps.corpus.by_project.get(e.get("project") or "", []):
                if it.type in PRIMARY_TYPES and it.evidence_id not in pool:
                    pool[it.evidence_id] = {"evidence_id": it.evidence_id, "text": it.text}
    return list(pool.values())


async def verifier(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        draft = dict(state["proposal_draft"])
        pool = evidence_pool(state, deps)
        facts = {"years_experience": deps.profile.get("years_experience")}
        rep = verify_and_repair(draft["cover_letter"], pool, facts)
        answers, a_removed = [], 0
        for a in draft.get("screening_answers", []):
            if not a.strip() or a == PLACEHOLDER:
                answers.append(a)
                continue
            ra = verify_and_repair(a, pool, facts)
            a_removed += len(ra.removed)
            answers.append(ra.clean_text or PLACEHOLDER)
        clean = rep.clean_text.strip()
        flagged_empty = not clean
        draft.update(cover_letter=clean, screening_answers=answers,
                     claims=[c.model_dump() for c in rep.claims])
        report = {"factual_claims_before": rep.factual_before, "unsupported_before": rep.unsupported_before,
                  "partly_before": rep.partly_before, "unsupported_after": rep.unsupported_after,
                  "rate_before": round(rep.rate_before, 4), "rate_after": round(rep.rate_after, 4),
                  "removed": rep.removed, "rewritten": rep.rewritten, "answers_removed": a_removed,
                  "evidence_pool": len(pool)}
        upd: dict[str, Any] = {"proposal_draft": draft, "claim_verification": [c.model_dump() for c in rep.claims],
                               "verification_report": report}
        if flagged_empty:  # never keep unverified text: an empty letter fails the Critic and triggers a rewrite (or needs_attention)
            upd["errors"] = ["verifier: no supported sentence left in the letter"]
        return finish("verifier", t0, upd, mode="deterministic", before=rep.unsupported_before, removed=len(rep.removed))
    except Exception as e:
        return fail("verifier", t0, e)
