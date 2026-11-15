"""Writer: drafts the proposal from evidence only (R5). The ONLY node with a free-text LLM output that reaches a client, so it is
followed by claim verification, the Critic and the Compliance Guard, and then a human.

LLM path: prompt agent/prompts/writer.md (job post quoted as untrusted data, evidence as the sole source of facts).
Fallback path (no LLM / invalid output): a conservative template built only from matched evidence and skill names, so the graph
still produces a safe, honest draft and the trace says mode=template. Opener strategy is assigned deterministically from
hash(job_id) over config `strategies` (X2 experiment arm, Phase 9).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from agent.handoffs import FitReport, ProposalDraft
from agent.security.quarantine import neutralise, quote_untrusted

from .common import Deps, fail, finish


class WriterOut(BaseModel):
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


def pick_strategy(job_id: str, strategies: dict[str, str]) -> str | None:
    if not strategies:
        return None
    names = sorted(strategies)
    return names[int(hashlib.sha1(job_id.encode()).hexdigest(), 16) % len(names)]


def _evidence_block(evidence: list[dict[str, Any]], limit: int = 6, chars: int = 700) -> str:
    return "\n".join(f'<evidence id="{e["evidence_id"]}">\n{neutralise(e["text"], chars)}\n</evidence>' for e in evidence[:limit])


def template_letter(job: dict[str, Any], fit: FitReport, evidence: list[dict[str, Any]], profile: dict[str, Any]) -> WriterOut:
    """Honest fallback: states only what matched evidence shows (project titles / skills named in that evidence)."""
    by_id = {e["evidence_id"]: e for e in evidence}
    lines = [f"Hello, thanks for posting \"{job.get('title', 'your project')}\"."]
    used: list[str] = []
    for m in fit.matches:
        for eid in m.evidence_ids[:1]:
            if eid in by_id and eid not in used:
                used.append(eid)
    # Wording avoids first-person experience claims ("I built..."): everything stated is a pointer to matched evidence, not a new fact.
    if used:
        lines.append(f"My portfolio includes evidence for {', '.join(fit.matched_skills[:4])}, and I can walk you through the relevant examples.")
        if fit.missing_skills:
            lines.append(f"Evidence for {', '.join(fit.missing_skills[:2])} is thinner, so I would confirm scope on that part first.")
    else:
        lines.append("I would like to understand the goal before proposing an approach.")
    lines.append("Before starting, I would confirm the data sources, the success criteria and how you will review early results, "
                 "so the first delivery is useful and the scope stays realistic.")
    lines.append("Could you share what a successful first milestone looks like for you? "
                 "Then I can outline a concrete first step and a realistic timeline.")
    answers = ["[needs your answer]" for _ in job.get("screening_questions", [])]
    return WriterOut(cover_letter=" ".join(lines), screening_answers=answers, evidence_ids=used)


async def writer(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job = state["job"]
        fit = FitReport.model_validate(state["fit_report"])
        evidence = state.get("evidence") or []
        known = {e["evidence_id"] for e in evidence}
        prev = state.get("critique") or {}
        rev = int(state.get("revision", 0)) + (1 if state.get("proposal_draft") and not prev.get("passed", True) else 0)
        strat = pick_strategy(job["job_id"], deps.cfg.get("strategies", {}))
        out: WriterOut | None = None
        results = []
        mode = "template"
        if deps.llm is not None:
            style = (state.get("memory_context") or {}).get("style") or []
            user = "\n".join([
                quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}\n"
                                + "\n".join(f"Q: {q}" for q in job.get("screening_questions", []))),
                _evidence_block(evidence) or "<evidence_none/>",
                "<profile>" + json.dumps({"name": deps.profile.get("display_name"), "years_experience": deps.profile.get("years_experience"),
                                          "skills": deps.profile.get("skills", [])[:25]}) + "</profile>",
                f"<style>{'; '.join(str(s) for s in style[:5])}</style>" if style else "",
                f"<opener>{deps.cfg.get('strategies', {}).get(strat, '')}</opener>" if strat else "",
                f"<revise>{'; '.join(prev.get('issues', [])[:4])}</revise>" if rev and prev.get("issues") else "",
            ])
            try:
                out, res = await deps.llm.complete_json("writer", "writer", user, WriterOut, max_tokens=900)
                results.append(res)
                mode = "llm"
            except Exception as e:
                mode = f"template(llm_error={type(e).__name__})"
        if out is None:
            out = template_letter(job, fit, evidence, deps.profile)
        n_q = len(job.get("screening_questions", []))
        answers = (out.screening_answers + ["[needs your answer]"] * n_q)[:n_q]
        ids = [i for i in out.evidence_ids if i in known] or ([e["evidence_id"] for e in evidence[:2]] if evidence else [])
        draft = ProposalDraft(job_id=job["job_id"], cover_letter=out.cover_letter.strip(), screening_answers=answers, evidence_ids=ids,
                              revision=rev, strategy=strat)
        upd: dict[str, Any] = {"proposal_draft": draft.model_dump(), "revision": rev}
        return finish("writer", t0, upd, results=results, mode=mode, revision=rev, strategy=strat)
    except Exception as e:
        return fail("writer", t0, e)
