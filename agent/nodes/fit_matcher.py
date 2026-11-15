"""Fit Matcher: maps each job requirement to verified portfolio evidence (R5: evidence ids, never self-declared skills).

Uses the RAG pipeline that SHIPS (config rag.variant) via `deps.retriever`. Per requirement it looks for a retrieved evidence item
(or any item of a retrieved project) that actually mentions the skill/tool. A skill that is only declared in the profile, with no
evidence, counts as MISSING: the writer must not claim it. The pipeline may abstain (no usable evidence): then fit is 0 and the
trace says so. Deterministic, no LLM.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from agent.handoffs import FitReport, JobSpec, RequirementMatch
from rag.requirements import mentions, requirements_from_spec, term_of
from rag.types import PRIMARY_TYPES, JobQuery

from .analyst import base_spec
from .common import Deps, fail, finish


def _query(job: dict[str, Any], spec: JobSpec) -> JobQuery:
    extra = [r.text for r in spec.requirements if r.kind != "skill"][:4]
    desc = job.get("description", "") + ("\n" + "; ".join(extra) if extra else "")
    return JobQuery(job_id=job["job_id"], title=job.get("title", ""), description=desc,
                    required_skills=list(spec.required_skills), screening_questions=list(spec.screening_questions))


def match_requirements(spec: JobSpec, corpus: Any, evidence_ids: list[str], projects: list[str]) -> list[RequirementMatch]:
    """Pure function (testable without a retriever). Candidate items = retrieved evidence + other items of retrieved projects."""
    cand: list[Any] = []
    seen: set[str] = set()
    for eid in evidence_ids:
        it = corpus.by_id.get(eid)
        if it is not None and it.evidence_id not in seen and it.type in PRIMARY_TYPES:
            seen.add(it.evidence_id)
            cand.append(it)
    for pr in projects:
        for it in corpus.by_project.get(pr, []):
            if it.evidence_id not in seen and it.type in PRIMARY_TYPES:
                seen.add(it.evidence_id)
                cand.append(it)
    out = []
    for req in requirements_from_spec(spec):
        if req.kind not in ("skill", "deliverable"):
            continue
        term = term_of(req)
        hits = [it for it in cand if mentions(it.text, term)]
        # retrieved items first (they are ranked), then project siblings
        hits.sort(key=lambda it: evidence_ids.index(it.evidence_id) if it.evidence_id in evidence_ids else 999)
        strength = min(1.0, 0.5 + 0.25 * len(hits)) if hits else 0.0
        out.append(RequirementMatch(requirement_id=req.id, evidence_ids=[h.evidence_id for h in hits[:3]],
                                    strength=round(strength, 3)))
    return out


async def fit_matcher(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job = state["job"]
        spec = JobSpec.model_validate(state["job_spec"]) if state.get("job_spec") else base_spec(job)  # parallel with the Analyst: own spec
        if deps.retriever is None or deps.corpus is None:
            raise RuntimeError("fit_matcher needs a retriever and corpus in Deps")
        k = int(deps.cfg.get("rag", {}).get("k", 5))
        ret = await asyncio.to_thread(deps.retriever.retrieve, _query(job, spec), k)
        matches = match_requirements(spec, deps.corpus, list(ret.evidence_ids), list(ret.projects))
        reqs = {r.id: r for r in requirements_from_spec(spec)}
        total = sum(1.0 if reqs[m.requirement_id].must_have else 0.5 for m in matches)
        got = sum((1.0 if reqs[m.requirement_id].must_have else 0.5) * m.strength for m in matches)
        score = 0.0 if ret.abstained or not matches else round(got / total, 3)
        matched = [term_of(reqs[m.requirement_id]) for m in matches if m.evidence_ids]
        missing = [term_of(reqs[m.requirement_id]) for m in matches if not m.evidence_ids]
        used = list(dict.fromkeys(e for m in matches for e in m.evidence_ids))
        ev_ids = list(dict.fromkeys([*ret.evidence_ids, *used]))[: max(k, 5) + 3]
        evidence = [{"evidence_id": e, "text": deps.corpus.by_id[e].text, "type": deps.corpus.by_id[e].type,
                     "project": deps.corpus.by_id[e].project} for e in ev_ids if e in deps.corpus.by_id]
        reasons = [f"matched {len(matched)}/{len(matches)} requirements with evidence"]
        if ret.abstained:
            reasons.append("retrieval abstained: no usable evidence")
        if missing:
            reasons.append("no evidence for: " + ", ".join(missing[:6]))
        rep = FitReport(score=score, matched_skills=matched, missing_skills=missing, evidence_ids=used or list(ret.evidence_ids),
                        matches=matches, reasons=reasons)
        meta = {"abstained": ret.abstained, **{k2: v for k2, v in (ret.meta or {}).items() if isinstance(v, int | float | str | bool)}}
        return finish("fit_matcher", t0, {"fit_report": rep.model_dump(), "evidence": evidence, "rag_meta": meta},
                      mode="deterministic", score=score, abstained=ret.abstained)
    except Exception as e:
        return fail("fit_matcher", t0, e)
