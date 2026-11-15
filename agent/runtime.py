"""Composition root: the ONE place that turns config + environment into the `Deps` every graph node receives.

Used by the API, the worker, the evals and the CLI, so a run behaves the same everywhere and nodes never import adapters, indexes or
models themselves. Everything optional degrades to the deterministic behaviour: no LLM keys -> template/rule modes; no classifier ->
heuristics only; no marketplace -> read-only dry runs.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.nodes.common import Deps
from agent.settings import ROOT, load_config


@lru_cache
def load_price_stats(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else ROOT / "data" / "reference" / "price_stats.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


USER_PERSONAS = Path("data") / "user" / "personas"  # the owner's OWN portfolio (uploaded in the UI); git-ignored


def user_persona_id(owner: str) -> str:
    return "user-" + re.sub(r"[^a-z0-9]", "", owner.lower())[:12]


def persona_dir(persona: str, cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    if persona.startswith("user-"):
        return ROOT / USER_PERSONAS / persona
    return ROOT / cfg["rag"].get("personas_dir", "data/synthetic/personas") / persona


def list_personas(cfg: dict[str, Any] | None = None) -> list[str]:
    root = persona_dir("", cfg)
    own = ROOT / USER_PERSONAS
    found = [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
    found += [p.name for p in own.iterdir() if p.is_dir() and (p / "profile.json").exists()] if own.exists() else []
    return sorted(found)


def persona_visible(persona: str, owner: str) -> bool:
    """Synthetic personas are shared demo data; a `user-*` portfolio belongs to exactly one account (another's is indistinguishable from missing)."""
    return not persona.startswith("user-") or persona == user_persona_id(owner)


def build_deps(persona: str, *, cfg: dict[str, Any] | None = None, llm: Any = None, market: Any = None, memory: Any = None,
               owner: str = "default", classifier: Any = None, slots: Callable[[], Any] | None = None,
               known_jobs: Callable[[], list[dict[str, Any]]] | None = None, with_retriever: bool = True) -> Deps:
    """Deps for one freelancer persona. `with_retriever=False` skips building the RAG index (cheap: used by tests and read-only calls)."""
    cfg = cfg or load_config()
    kw: dict[str, Any] = {}
    if with_retriever:
        from rag.registry import build_context, build_retriever

        ctx = build_context(persona_dir(persona, cfg), cfg)
        kw.update(retriever=build_retriever("vbest", ctx, cfg), corpus=ctx.corpus, profile=dict(ctx.corpus.profile))
    d = Deps(cfg=cfg, llm=llm, persona=persona, price_stats=load_price_stats(), market=market, memory=memory, owner=owner,
             injection_classifier=classifier, **kw)
    if slots is not None:
        d.slots = slots
    if known_jobs is not None:
        d.known_jobs = known_jobs
    return d


def default_llm(cfg: dict[str, Any] | None = None) -> Any:
    """An LLMRouter when at least one provider key for the active profile exists, else None (deterministic modes)."""
    import os

    from agent.llm import LLMRouter
    from agent.settings import active_profile

    cfg = cfg or load_config()
    provs = cfg["models"]["profiles"][active_profile(cfg)]["providers"]
    has_key = any(os.environ.get(p.get("api_key_env") or "") for p in provs.values() if p.get("api_key_env"))
    has_local = "ollama" in provs and os.environ.get("USE_OLLAMA") == "1"
    return LLMRouter(cfg=cfg) if has_key or has_local else None
