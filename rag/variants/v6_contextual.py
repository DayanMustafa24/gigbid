"""V6 (Indexing group): contextual retrieval. A context line (project, client industry, year, skills; optionally
LLM-written with llm=True) is prepended to each chunk before BM25 and embedding. Targets fragments that lose meaning alone."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(contextual=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v6_contextual", FLAGS, ctx)
