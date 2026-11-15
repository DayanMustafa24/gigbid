"""V1 (Query group, required): requirement decomposition. One retrieval per extracted requirement, then a
requirement-diverse merge. Targets jobs that ask for several different things."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(decompose=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v1_decompose", FLAGS, ctx)
