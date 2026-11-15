"""V0: naive dense baseline. Fixed-size chunks, top-k cosine on the WHOLE job post. The number everything must beat."""
from __future__ import annotations

from ..index import UnitIndex
from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags()


def retrieve(query: str, index: UnitIndex, k: int = 5) -> list[dict]:
    """Starter-kit signature: chunk-level top-k over a UnitIndex, de-duplicated to evidence items."""
    scores = index.dense(query)
    out: list[dict] = []
    seen: set[str] = set()
    for i in index.rank(scores, len(index)):
        u = index.units[i]
        if u.evidence_ids[0] in seen:
            continue
        seen.add(u.evidence_ids[0])
        out.append({"evidence_id": u.evidence_ids[0], "score": float(scores[i]), "text": u.text})
        if len(out) == k:
            break
    return out


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v0_naive", FLAGS, ctx)
