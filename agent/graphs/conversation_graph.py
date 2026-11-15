"""Durable conversation graph (assignment C, "conversation_graph"): one inbound client message -> intent + reply draft -> human -> send.

  START -> load_context -> client_liaison --(stopped)--> conv_dead_letter -> END
                                          \\--> message_gate (interrupt: approve | edit | dismiss)
                                                 --(approved|edited)--> message_sender -> END
                                                 \\--(dismissed|blocked_after_edit)--> END

The liaison scans the message with the policy rules BEFORE any LLM sees it (injection, off-platform contact/payment). A flagged message
never gets an auto-drafted reply. Needs a checkpointer (MemorySaver in tests, durable saver in production) or `interrupt()` cannot pause.
"""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.graphs.bid_graph import _bind
from agent.nodes.client_liaison import client_liaison
from agent.nodes.common import Deps
from agent.nodes.conversation_gates import (
    conv_dead_letter,
    conversation_stop_reason,
    load_context,
    message_gate,
    message_sender,
)
from agent.state import GigBidState


def after_liaison(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    return "conv_dead_letter" if conversation_stop_reason(state, cfg) else "message_gate"


def after_gate(state: dict[str, Any]) -> str:
    return "message_sender" if state.get("approval_status") in ("approved", "edited") else END


def build_conversation_graph(deps: Deps | None = None, checkpointer: Any = None):
    deps = deps or Deps()
    g = StateGraph(GigBidState)
    for name, fn in (("load_context", load_context), ("client_liaison", client_liaison), ("message_gate", message_gate),
                     ("message_sender", message_sender), ("conv_dead_letter", conv_dead_letter)):
        g.add_node(name, _bind(fn, deps))
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "client_liaison")
    g.add_conditional_edges("client_liaison", partial(after_liaison, cfg=deps.cfg),
                            {"message_gate": "message_gate", "conv_dead_letter": "conv_dead_letter"})
    g.add_conditional_edges("message_gate", after_gate, {"message_sender": "message_sender", END: END})
    g.add_edge("message_sender", END)
    g.add_edge("conv_dead_letter", END)
    return g.compile(checkpointer=checkpointer)
