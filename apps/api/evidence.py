"""Evidence lookup for the UI's hover cards: the portfolio items behind a claim or a requirement match.

Reads the same corpus the retriever uses (rag.ingest.loaders), cached per persona folder. Only citable item types are served
(case study, README, code, CV bullet, review): past proposals are never evidence. Text is capped; nothing here writes anything.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from rag.ingest.loaders import load_persona
from rag.types import PRIMARY_TYPES

MAX_IDS = 30
MAX_TEXT = 1200


@lru_cache(maxsize=8)
def _corpus(path: str):
    return load_persona(path)


def lookup(persona_path: Path | str, ids: list[str]) -> list[dict[str, Any]]:
    corpus = _corpus(str(persona_path))
    out: list[dict[str, Any]] = []
    for eid in dict.fromkeys(ids[:MAX_IDS]):  # de-duplicated, order kept
        it = corpus.by_id.get(eid)
        if it is None or it.type not in PRIMARY_TYPES:
            continue
        out.append({"evidence_id": it.evidence_id, "type": it.type, "project": it.project, "project_title": it.project_title,
                    "text": it.text[:MAX_TEXT], "truncated": len(it.text) > MAX_TEXT, "date": it.date,
                    "client_industry": it.client_industry, "rating": it.rating})
    return out
