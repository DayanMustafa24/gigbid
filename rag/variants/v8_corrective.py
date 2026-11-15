"""V8 (Agentic group): corrective loop. Grade the evidence per requirement, re-query exact terms when weak, and ABSTAIN
(return no evidence, flag unsupported requirements) when nothing backs the job. Targets jobs the persona should skip."""
from __future__ import annotations

from .pipeline import ComposedRetriever, Flags, RagContext

FLAGS = Flags(corrective=True)


def build(ctx: RagContext) -> ComposedRetriever:
    return ComposedRetriever("v8_corrective", FLAGS, ctx)
