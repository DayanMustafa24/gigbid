"""V4 (Ranking group): outcome-aware ranking. Boosts evidence from projects that past proposals which WON or got REPLIES
talked about (smoothed, capped at +-outcome_beta). Learning from what actually worked."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(outcome=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v4_outcome", FLAGS, ctx)
