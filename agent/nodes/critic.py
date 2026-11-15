"""Critic: reviews the VERIFIED draft for grounding, personalisation and length; the graph allows at most `budgets.max_revisions` rewrites.

`passed` is decided by measurable checks against config `critic` thresholds and the policy word limits, so it is reproducible and cannot
be talked into approval. The optional LLM review (prompt agent/prompts/critic.md) only ADDS concrete issues (and may mark a letter
generic); it can fail a draft, never rescue one that fails a measured check.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from agent.handoffs import Critique, FitReport, JobSpec
from agent.policy.engine import load_policy
from agent.security.quarantine import quote_untrusted
from rag.requirements import mentions

from .common import Deps, fail, finish


class CriticOut(BaseModel):
    issues: list[str] = Field(default_factory=list)
    generic: bool = False


def measure(draft: dict[str, Any], verification: list[dict[str, Any]], fit: FitReport, spec: JobSpec, cfg: dict[str, Any]) -> tuple[Critique, dict[str, Any]]:
    cc, lim = cfg["critic"], load_policy().limits
    text = draft.get("cover_letter", "")
    words = len(text.split())
    labelled = [c for c in verification if c.get("label")]
    supported = sum(c["label"] == "supported" for c in labelled)
    grounding = round(supported / len(labelled), 3) if labelled else 1.0
    terms = fit.matched_skills or spec.required_skills
    pers = round(sum(mentions(text, t) for t in terms) / len(terms), 3) if terms else 1.0
    length_ok = lim.get("min_cover_letter_words", 0) <= words <= lim.get("max_cover_letter_words", 10**6)
    issues: list[str] = []
    if grounding < cc["min_grounding"]:
        issues.append(f"only {grounding:.0%} of factual claims were fully supported by evidence")
    if pers < cc["min_personalisation"]:
        issues.append(f"addresses only {pers:.0%} of the job's key skills")
    if not length_ok:
        issues.append(f"{words} words: outside {lim.get('min_cover_letter_words')}-{lim.get('max_cover_letter_words')}")
    passed = not issues
    return Critique(passed=passed, issues=issues, grounding=grounding, personalisation=pers, length_ok=length_ok), {"words": words}


async def critic(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job, draft = state["job"], state["proposal_draft"]
        crit, facts = measure(draft, state.get("claim_verification") or [], FitReport.model_validate(state["fit_report"]),
                              JobSpec.model_validate(state["job_spec"]), deps.cfg)
        results, mode = [], "deterministic"
        if deps.llm is not None and draft.get("cover_letter"):
            user = "\n".join([
                quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}"),
                f"<draft>\n{draft['cover_letter']}\n</draft>",
                f"<checks>grounding={crit.grounding} personalisation={crit.personalisation} words={facts['words']}</checks>"])
            try:
                out, res = await deps.llm.complete_json("critic", "critic", user, CriticOut, max_tokens=300)
                results.append(res)
                mode = "llm+deterministic"
                extra = [i for i in out.issues[:4] if i not in crit.issues]
                crit = crit.model_copy(update={"issues": crit.issues + extra, "passed": crit.passed and not extra and not out.generic})
                if out.generic and "generic" not in " ".join(crit.issues).lower():
                    crit.issues.append("letter is generic: could be sent to any client")
            except Exception as e:
                mode = f"deterministic(llm_error={type(e).__name__})"
        return finish("critic", t0, {"critique": crit.model_dump()}, results=results, mode=mode, passed=crit.passed,
                      revision=int(state.get("revision", 0)))
    except Exception as e:
        return fail("critic", t0, e)
