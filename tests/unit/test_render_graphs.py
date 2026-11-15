"""The architecture diagrams are generated from the compiled graphs, so these tests also guard the graph topology."""
from __future__ import annotations

from scripts.render_graphs import graphs, mermaid_for, render


def test_mermaid_contains_every_node_and_the_human_gates():
    g = graphs()
    bid = mermaid_for(g["bid_graph"])
    for node in ("intake", "injection_guard", "analyst", "risk_assessor", "fit_matcher", "join", "decision", "pricing", "writer", "verifier",
                 "critic", "compliance", "approval_gate", "executor", "dead_letter", "not_worth"):
        assert node in bid, node
    conv = mermaid_for(g["conversation_graph"])
    for node in ("load_context", "client_liaison", "message_gate", "message_sender", "conv_dead_letter"):
        assert node in conv, node


def test_render_writes_files(tmp_path):
    files = render(tmp_path)
    assert {f.name for f in files} == {"bid_graph.mmd", "conversation_graph.mmd"} and all(f.read_text().startswith("---") or "graph" in f.read_text()
                                                                                    for f in files)
