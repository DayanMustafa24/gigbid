"""Regenerate ONE paragraph of a pending proposal (assignment: Proposal studio). Stateless and side-effect free.

Rewrites the chosen paragraph with the model (prompt agent/prompts/paragraph.md; job post quoted as untrusted data, evidence as the sole
source of facts), then runs the SAME safety passes as a fresh draft: claim verification against the evidence pool, then the policy guard.
The result is only a CANDIDATE: the UI loads it into the editor and the human submits it through the normal `edit` decision, which is
checked again and bound to the human's own text (R4). Nothing here touches the graph checkpoint or the marketplace.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from agent.policy.engine import check_draft
from agent.security.quarantine import neutralise, quote_untrusted
from rag.verify import verify_and_repair

from .common import Deps
from .verifier import evidence_pool
from .writer import _evidence_block


class ParagraphOut(BaseModel):
    paragraph: str
    evidence_ids: list[str] = Field(default_factory=list)


class RegenerateError(Exception):
    """Carries a stable `code` the API maps to an HTTP error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def split_paragraphs(letter: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", letter) if p.strip()]


async def regenerate_paragraph(state: dict[str, Any], deps: Deps, index: int, instruction: str | None = None) -> dict[str, Any]:
    draft = state.get("proposal_draft") or {}
    paras = split_paragraphs(draft.get("cover_letter", ""))
    if not 0 <= index < len(paras):
        raise RegenerateError("paragraph_out_of_range", f"The letter has {len(paras)} paragraph(s); index {index} does not exist")
    if deps.llm is None:
        raise RegenerateError("model_unavailable", "No language model is configured, so a paragraph cannot be regenerated")
    job, evidence = state["job"], state.get("evidence") or []
    user = "\n".join([
        quote_untrusted("job_post", f"{job.get('title', '')}\n{job.get('description', '')}"),
        _evidence_block(evidence) or "<evidence_none/>",
        "<letter>\n" + "\n\n".join(paras) + "\n</letter>",
        "<target>\n" + paras[index] + "\n</target>",
        f"<instruction>{neutralise(instruction, 300)}</instruction>" if instruction else "",
    ])
    try:
        out, _ = await deps.llm.complete_json("writer", "paragraph", user, ParagraphOut, max_tokens=500)
    except Exception as e:
        raise RegenerateError("model_failed", f"The model did not return a usable paragraph ({type(e).__name__})") from e
    known = {e["evidence_id"] for e in evidence}
    rep = verify_and_repair(out.paragraph.strip(), evidence_pool(state, deps), {"years_experience": deps.profile.get("years_experience")})
    clean = rep.clean_text.strip()
    if not clean:
        raise RegenerateError("nothing_supported", "Every sentence of the new paragraph was unsupported by your portfolio; try another instruction")
    new = [*paras[:index], clean, *paras[index + 1:]]
    letter = "\n\n".join(new)
    verdict = check_draft(letter, list(draft.get("screening_answers", [])))
    if verdict.cleaned_cover_letter is not None:
        letter = verdict.cleaned_cover_letter
    return {"paragraph": index, "paragraph_text": clean, "cover_letter": letter, "removed_claims": [str(x) for x in rep.removed],
            "violations": [v.rule_id for v in verdict.violations], "evidence_ids": [i for i in out.evidence_ids if i in known]}
