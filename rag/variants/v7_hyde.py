"""V7 (Query group): HyDE. Retrieve with a hypothetical ideal proposal for the job (LLM if llm=True, else a template;
the mode is recorded in each result's meta). Targets vague client posts."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(hyde=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v7_hyde", FLAGS, ctx)
