"""V3 (Retrieval group, required): cross-encoder reranking of the dense top-N. Targets precision at small k.
Reranker comes from config (rag.reranker): lexical stand-in offline, bge-reranker-v2-m3 for the real measurement."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(rerank=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v3_rerank", FLAGS, ctx)
