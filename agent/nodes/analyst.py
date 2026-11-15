"""Job Analyst: structured JobSpec from an untrusted job post. Tool-less by design (injection defence, config allowlist: analyst: {}).

The deterministic base comes from the job's own structured fields + rag.requirements; the LLM (prompt agent/prompts/analyst.md)
may ADD deliverables, hidden needs and requirements. If the LLM is missing or its output is invalid, the deterministic spec is
returned and the trace says mode=deterministic. Nothing the post says can change behaviour: the post is quoted data.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from agent.handoffs import JobSpec, Requirement
from agent.security.quarantine import quote_untrusted
from rag.requirements import PREFIX, _clean, extract_requirements
from rag.types import JobQuery

from .common import Deps, fail, finish


class _Req(BaseModel):
    text: str
    kind: str = "skill"
    must_have: bool = True


class AnalystOut(BaseModel):
    deliverables: list[str] = Field(default_factory=list)
    requirements: list[_Req] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list)


def _budget(job: dict[str, Any]) -> tuple[float | None, float | None, str]:
    b = job.get("budget") or {}
    return b.get("min"), b.get("max"), b.get("currency", "USD")


def base_spec(job: dict[str, Any]) -> JobSpec:
    q = JobQuery.from_dev_row({"job_id": job["job_id"], "title": job.get("title", ""), "description": job.get("description", ""),
                               "required_skills": job.get("required_skills", []),
                               "screening_questions": job.get("screening_questions", [])})
    lo, hi, cur = _budget(job)
    return JobSpec(job_id=job["job_id"], title=job.get("title", ""), required_skills=_clean(list(job.get("required_skills", [])), 12),
                   requirements=extract_requirements(q), screening_questions=list(job.get("screening_questions", [])),
                   budget_min=lo, budget_max=hi, currency=cur, duration_days=job.get("duration_days"),
                   connects_cost=job.get("connects_cost"), category=job.get("category"))


def _merge(spec: JobSpec, out: AnalystOut) -> JobSpec:
    have = {r.text.lower() for r in spec.requirements}
    reqs = list(spec.requirements)
    for r in out.requirements:
        kind = r.kind if r.kind in ("skill", "deliverable", "constraint", "hidden") else "skill"
        text = r.text.strip()
        text = text if kind != "skill" or text.startswith(PREFIX) else PREFIX + text
        if text and text.lower() not in have and len(reqs) < 12:
            have.add(text.lower())
            reqs.append(Requirement(id=f"r{len(reqs) + 1}", text=text, kind=kind, must_have=r.must_have))  # type: ignore[arg-type]
    return spec.model_copy(update={"requirements": reqs, "deliverables": out.deliverables[:8], "hidden_needs": out.hidden_needs[:6]})


async def analyst(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        job = state["job"]
        spec = base_spec(job)
        if deps.llm is None:
            return finish("analyst", t0, {"job_spec": spec.model_dump()}, mode="deterministic")
        user = quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}\n"
                               + "\n".join(job.get("screening_questions", [])))
        try:
            out, res = await deps.llm.complete_json("analyst", "analyst", user, AnalystOut, max_tokens=600)
        except Exception as e:  # LLM down / invalid output: deterministic spec is still correct and safe
            return finish("analyst", t0, {"job_spec": spec.model_dump()}, mode="deterministic", llm_error=type(e).__name__)
        return finish("analyst", t0, {"job_spec": _merge(spec, out).model_dump()}, results=[res], mode="llm")
    except Exception as e:
        return fail("analyst", t0, e)
