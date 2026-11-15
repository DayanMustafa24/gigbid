"""Requirement extraction for the retrieval side (V1 decomposition, V8 grading).

Deterministic default: uses the structured fields a job already has (required_skills, a "Must have:" line, a "Skills:"
line). In the live graph the Job Analyst agent produces a richer JobSpec; `requirements_from_spec` adapts it. An optional
LLM extractor (prompt: agent/prompts/rag_decompose.md) is used only when the pipeline is built with llm=True.
"""
from __future__ import annotations

import re

from agent.handoffs import JobSpec, Requirement

from .types import JobQuery

_MUST = re.compile(r"(?:must have|requirements?|skills?|looking for)\s*:\s*([^.\n]+)", re.I)
PREFIX = "Experience with "


def _clean(skills: list[str], cap: int = 8) -> list[str]:
    seen, out = set(), []
    for s in skills:
        s = s.strip(" .;")
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:cap]


def extract_requirements(job: JobQuery) -> list[Requirement]:
    skills = _clean(job.required_skills)
    if not skills:
        m = _MUST.search(job.description)
        if m:
            skills = _clean(re.split(r",|/|\band\b", m.group(1)))
    return [Requirement(id=f"r{i}", text=PREFIX + s, kind="skill", must_have=True) for i, s in enumerate(skills, 1)]


def extract_requirements_llm(job: JobQuery, llm) -> list[Requirement]:
    """LLM extractor (prompt agent/prompts/rag_decompose.md). Any failure or bad output -> deterministic extractor."""
    import json

    from .llm import load_prompt

    try:
        sys_p, _ = load_prompt("rag_decompose")
        raw = llm.complete(sys_p, f"<job_post>\n{job.whole_post[:1500]}\n</job_post>", max_tokens=200)
        raw = raw[raw.index("[") : raw.rindex("]") + 1]
        skills = _clean([str(x) for x in json.loads(raw) if isinstance(x, str)])
    except Exception:
        skills = []
    if not skills:
        return extract_requirements(job)
    return [Requirement(id=f"r{i}", text=PREFIX + s, kind="skill", must_have=True) for i, s in enumerate(skills, 1)]


def requirements_from_spec(spec: JobSpec) -> list[Requirement]:
    return list(spec.requirements) or [
        Requirement(id=f"r{i}", text=PREFIX + s) for i, s in enumerate(_clean(spec.required_skills), 1)
    ]


def term_of(req: Requirement) -> str:
    """The skill/tool name inside a requirement ('Experience with RAG' -> 'RAG')."""
    return req.text[len(PREFIX):] if req.text.startswith(PREFIX) else req.text


def mentions(text: str, term: str) -> bool:
    """Word-boundary, case-insensitive presence of `term` (handles C++, Node.js, scikit-learn)."""
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", text, re.I) is not None
