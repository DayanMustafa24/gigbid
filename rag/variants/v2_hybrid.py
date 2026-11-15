"""V2 (Retrieval group, required): hybrid BM25 + dense fused with reciprocal rank fusion.
Targets exact tool and library names ("Celery", "dbt", "Pinecone") that embeddings blur."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(hybrid=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v2_hybrid", FLAGS, ctx)
