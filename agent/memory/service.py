"""Glue between a finished graph run and long-term memory. Called by the API/worker (Phase 6), never from inside a graph, so a graph
run stays side-effect free apart from the marketplace write after approval (State / trace / memory stay separate)."""
from __future__ import annotations

from typing import Any

from .store import MemoryStore


def learn_from_run(store: MemoryStore, owner: str, state: dict[str, Any]) -> dict[str, Any]:
    """Style learning from the human decision at the approval gate. Only approved / edited drafts teach anything."""
    ap = state.get("approval") or {}
    status = state.get("approval_status")
    if status not in ("approved", "edited", "submitted") or not ap.get("original_text"):
        return {"recorded": False, "promoted": []}
    edited = ap.get("edited_text")  # None when approved as is
    persona = state.get("persona", "")
    recorded = store.record_style_event(owner, persona, state.get("job_id", ""), ap["original_text"], edited,
                                        "edited" if edited else "approved")
    return {"recorded": recorded, "promoted": store.promote_style(owner, persona) if recorded else []}


def learn_outcome(store: MemoryStore, owner: str, persona: str, proposal_key: str, strategy: str | None, outcome: str,
                  category: str | None = None) -> None:
    store.record_outcome(owner, persona, proposal_key, strategy, outcome, category=category)
