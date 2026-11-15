"""Variant registry + factory. `variant` in agent/config.yaml (rag.variant) picks what ships; evals compare them all."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.settings import load_config

from .embeddings import make_embedder, make_reranker
from .ingest.loaders import load_persona
from .llm import LLM
from .variants import (
    v0_naive,
    v1_decompose,
    v2_hybrid,
    v3_rerank,
    v4_outcome,
    v5_multivector,
    v6_contextual,
    v7_hyde,
    v8_corrective,
)
from .variants.pipeline import ComposedRetriever, Flags, RagContext

PRESETS: dict[str, Flags] = {
    "v0_naive": v0_naive.FLAGS,
    "v1_decompose": v1_decompose.FLAGS,
    "v2_hybrid": v2_hybrid.FLAGS,
    "v3_rerank": v3_rerank.FLAGS,
    "v4_outcome": v4_outcome.FLAGS,
    "v5_multivector": v5_multivector.FLAGS,
    "v6_contextual": v6_contextual.FLAGS,
    "v7_hyde": v7_hyde.FLAGS,
    "v8_corrective": v8_corrective.FLAGS,
}
# Hypothesis-driven starting point (docs/RD_B_hypothesis.md); config `rag.best` overrides it after measurement.
DEFAULT_BEST = Flags(decompose=True, hybrid=True, rerank=True, corrective=True)
COMPONENTS = ("decompose", "hybrid", "rerank", "outcome", "multivector", "contextual", "hyde", "corrective")


def best_flags(cfg: dict[str, Any] | None = None) -> Flags:
    b = ((cfg or load_config()).get("rag") or {}).get("best")
    return Flags(**{k: bool(v) for k, v in b.items()}) if b else DEFAULT_BEST


def all_variants(cfg: dict[str, Any] | None = None) -> dict[str, Flags]:
    return {**PRESETS, "vbest": best_flags(cfg)}


def build_context(persona_dir: Path | str, cfg: dict[str, Any] | None = None, llm: LLM | None = None,
                  *, embedder=None, reranker=None) -> RagContext:
    rag = (cfg or load_config()).get("rag") or {}
    return RagContext(
        load_persona(persona_dir),
        embedder or make_embedder(rag.get("embedder")),
        reranker or make_reranker(rag.get("reranker")),
        llm,
        rag.get("params"),
    )


def build_retriever(name: str, ctx: RagContext, cfg: dict[str, Any] | None = None, *, llm: bool = False) -> ComposedRetriever:
    flags = all_variants(cfg).get(name)
    if flags is None:
        raise KeyError(f"unknown variant {name!r}; known: {sorted(all_variants(cfg))}")
    return ComposedRetriever(name, replace(flags, llm=llm), ctx)


def ablations(best: Flags) -> dict[str, Flags]:
    """Leave-one-out: the best pipeline with each ENABLED component switched off, one at a time."""
    return {f"-{c}": replace(best, **{c: False}) for c in COMPONENTS if getattr(best, c)}
