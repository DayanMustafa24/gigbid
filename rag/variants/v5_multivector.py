"""V5 (Indexing group): multi-vector items. Summary / README / code views are indexed separately and returned as one
project bundle. Targets repos that look irrelevant from their README alone."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(multivector=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v5_multivector", FLAGS, ctx)
