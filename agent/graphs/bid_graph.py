"""Hand-written LangGraph StateGraph for one job (assignment R3/R4). Not a linear chain:

  START -> intake -> injection_guard -> Send fan-out [analyst || risk_assessor || fit_matcher] (parallel, each writes its own state key)
        -> join -> decision --(skip|watch)--> END
                           \\--(bid)--> pricing --(not worth)--> not_worth -> END
                                                \\--> writer -> verifier -> critic --(failed, revisions left)--> writer
                                                                                 \\--> compliance --(blocked)--> dead_letter -> END
                                                                                                  \\--> approval_gate (interrupt)
                                                                                                         --(approve|edit)--> executor -> END
                                                                                                         \\--(reject)--> END
Every router first asks `stop_reason` (budgets, failed branches, policy block): a stopped run becomes `needs_attention` + `dead_letter`,
never a silent drop. Dependencies (models, retriever, marketplace) are injected via `Deps`, so tests run fully offline.
Use a checkpointer (MemorySaver in tests, Postgres in production) or `interrupt()` cannot pause.
"""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.nodes.analyst import analyst
from agent.nodes.common import Deps
from agent.nodes.compliance import compliance
from agent.nodes.critic import critic
from agent.nodes.decision import decision
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.gates import approval_gate, dead_letter, executor, intake, join, not_worth, stop_reason
from agent.nodes.injection_guard import injection_guard
from agent.nodes.pricing import pricing
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.verifier import verifier
from agent.nodes.writer import writer
from agent.state import GigBidState

PARALLEL = ("analyst", "risk_assessor", "fit_matcher")


def _bind(fn, deps: Deps):
    async def run(state):  # deliberately unannotated: LangGraph would otherwise try to infer a schema from the hint
        return await fn(state, deps)

    run.__name__ = fn.__name__
    return run


def fan_out(state: dict[str, Any]) -> list[Send]:
    return [Send(n, state) for n in PARALLEL]


def after_join(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    return "dead_letter" if stop_reason(state, cfg) else "decision"


def after_decision(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    return "pricing" if state.get("decision") == "bid" and not stop_reason(state, cfg) else END


def after_pricing(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    if stop_reason(state, cfg):
        return "dead_letter"
    return "writer" if (state.get("price_quote") or {}).get("connects_worth_it", False) else "not_worth"


def after_critic(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    if stop_reason(state, cfg):
        return "dead_letter"
    passed = (state.get("critique") or {}).get("passed", False)
    return "compliance" if passed or int(state.get("revision", 0)) >= cfg["budgets"]["max_revisions"] else "writer"


def after_compliance(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    return "dead_letter" if stop_reason(state, cfg) else "approval_gate"


def after_approval(state: dict[str, Any]) -> str:
    return "executor" if state.get("approval_status") in ("approved", "edited") else END


def build_bid_graph(deps: Deps | None = None, checkpointer: Any = None):
    deps = deps or Deps()
    cfg = deps.cfg
    g = StateGraph(GigBidState)
    for name, fn in (("intake", intake), ("injection_guard", injection_guard), ("analyst", analyst), ("risk_assessor", risk_assessor), ("fit_matcher", fit_matcher),
                     ("join", join),
                     ("decision", decision), ("pricing", pricing), ("not_worth", not_worth), ("writer", writer),
                     ("verifier", verifier), ("critic", critic), ("compliance", compliance), ("approval_gate", approval_gate),
                     ("executor", executor), ("dead_letter", dead_letter)):
        g.add_node(name, _bind(fn, deps))
    g.add_edge(START, "intake")
    g.add_edge("intake", "injection_guard")  # normalise + score untrusted text BEFORE any branch reads it
    g.add_conditional_edges("injection_guard", fan_out, list(PARALLEL))
    g.add_edge(list(PARALLEL), "join")  # a join waits for ALL three branches
    g.add_conditional_edges("join", partial(after_join, cfg=cfg), {"decision": "decision", "dead_letter": "dead_letter"})
    g.add_conditional_edges("decision", partial(after_decision, cfg=cfg), {"pricing": "pricing", END: END})
    g.add_conditional_edges("pricing", partial(after_pricing, cfg=cfg), {"writer": "writer", "not_worth": "not_worth", "dead_letter": "dead_letter"})
    g.add_edge("writer", "verifier")
    g.add_edge("verifier", "critic")
    g.add_conditional_edges("critic", partial(after_critic, cfg=cfg), {"writer": "writer", "compliance": "compliance", "dead_letter": "dead_letter"})
    g.add_conditional_edges("compliance", partial(after_compliance, cfg=cfg), {"approval_gate": "approval_gate", "dead_letter": "dead_letter"})
    g.add_conditional_edges("approval_gate", after_approval, {"executor": "executor", END: END})
    for terminal in ("not_worth", "executor", "dead_letter"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)
