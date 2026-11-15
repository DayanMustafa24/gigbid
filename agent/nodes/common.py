"""Shared plumbing for graph nodes: dependencies, uniform state updates, small text helpers.

Every node is `async def node(state, deps) -> dict` returning a PARTIAL state update. `finish()` adds the bookkeeping every node
must report (one step, one trace event, token/cost usage) so budgets (R8) and traces are uniform. `deps` is built once per run
(agent/graphs/deps wiring in 5.3) so nodes never import adapters, models or indexes directly.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent.llm import ChatResult, LLMRouter
from agent.settings import load_config
from agent.state import add_usage

_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


@dataclass
class Deps:
    """Everything a node may use. Any field can be None/empty: nodes then fall back to deterministic behaviour."""

    cfg: dict[str, Any] = field(default_factory=load_config)
    llm: LLMRouter | None = None
    retriever: Any = None  # rag ComposedRetriever (has .retrieve(JobQuery, k))
    corpus: Any = None  # rag Corpus of the active freelancer (evidence texts, profile)
    profile: dict[str, Any] = field(default_factory=dict)  # freelancer profile.json (rate, minimum budget, years, skills)
    price_stats: dict[str, Any] = field(default_factory=dict)  # data/reference/price_stats.json
    persona: str = ""
    known_jobs: Callable[[], list[dict[str, Any]]] = lambda: []  # already-seen jobs, for duplicate detection  # noqa: E731
    market: Any = None  # MarketplacePort (Scout/intake/executor/sender only; analyst/writer never get it)
    injection_classifier: Any = None  # optional async/sync callable text -> injection score 0..1 (e.g. Prompt Guard); None = heuristics only
    memory: Any = None  # MemoryStore: read at intake into state['memory_context'] (style rules, measured outcome rates); never written by nodes
    owner: str = "default"  # tenant id for memory reads
    slots: Callable[[], Any] = lambda: []  # free calendar slots (sync or async callable), e.g. wrapping the Workspace MCP get_free_slots  # noqa: E731
    approval: Any = None  # agent.approvals.ApprovalGrant: set by the API ONLY after a human decision; None = no token can be minted (writes fail closed)


def approval_kwargs(deps: Deps, action: str, subject: str, text: str | None = None) -> dict[str, Any]:
    """`approval_token=...` for a spending write, minted from the human's grant (None without one). Direct adapters ignore the argument;
    the Marketplace MCP server refuses the write without a valid token, so a missing grant fails closed."""
    return {} if deps.approval is None else {"approval_token": deps.approval.mint(action, subject, text=text)}


def finish(node: str, t0: float, update: dict[str, Any], *, results: list[ChatResult] | tuple[ChatResult, ...] = (),
           **note: Any) -> dict[str, Any]:
    """Add steps/events/usage to a node's update. `note` goes into the trace event (mode, counts, flags...)."""
    usage: dict[str, float] = {}
    for r in results:
        usage = add_usage(usage, r.usage())
    ev: dict[str, Any] = {"node": node, "ms": round((time.perf_counter() - t0) * 1000, 2), **note}
    if results:
        ev.update(model=results[-1].model, prompt_sha=results[-1].prompt_sha, fallback_used=any(r.fallback_used for r in results))
    update["steps"] = 1
    update["events"] = [ev]
    if usage:
        update["usage"] = usage
    return update


def fail(node: str, t0: float, err: Exception, **extra: Any) -> dict[str, Any]:
    """A node that cannot proceed records the error; the graph decides (retry / needs_attention). Never a guess."""
    return finish(node, t0, {"errors": [f"{node}: {type(err).__name__}: {err}"], **extra}, mode="error")


def tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / (len(ta | tb) or 1)


def job_text(job: dict[str, Any]) -> str:
    return "\n".join([job.get("title", ""), job.get("description", ""), *job.get("screening_questions", [])])
