"""Phase 5.7 tests: budgets, loop caps and dead-letter. A run that cannot finish within its budgets must surface as needs_attention with
a reason, never loop forever, never drop silently, and never reach the approval gate half-baked."""
from __future__ import annotations

from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver

from agent.graphs.bid_graph import build_bid_graph
from agent.nodes.common import Deps
from agent.nodes.conversation_gates import conversation_stop_reason
from agent.nodes.gates import stop_reason
from agent.settings import load_config
from rag.types import Item, Retrieval

CFG = load_config()
PROFILE = {"display_name": "Priya", "hourly_rate_usd": 55, "minimum_project_budget_usd": 300, "years_experience": 6, "skills": ["RAG"]}
OK = {"job_spec": {"x": 1}, "risk_report": {"x": 1}, "fit_report": {"x": 1}}


def job():
    return {"job_id": "j1", "client_id": "c1", "title": "RAG chatbot", "description": "Need a RAG chatbot built with FastAPI.",
            "required_skills": ["RAG", "FastAPI"], "screening_questions": ["Why you?"],
            "budget": {"min": 100, "max": 500, "type": "fixed", "currency": "USD"}, "connects_cost": 8, "category": "AI Chatbot Development"}


class Retr:
    def retrieve(self, q, k):
        return Retrieval(evidence_ids=["e1"], projects=["proj1"])


def deps(cfg=None):
    e1 = Item("e1", "p", "case_study", "proj1", "Built RAG with pgvector and FastAPI for a legal client")
    corpus = SimpleNamespace(by_id={"e1": e1}, by_project={"proj1": [e1]})
    d = Deps(retriever=Retr(), corpus=corpus, profile=PROFILE, persona="ai_ml")
    if cfg is not None:
        d.cfg = cfg
    return d


def tid(n):
    return {"configurable": {"thread_id": n}}


def test_stop_reason_checks_every_budget_in_order():
    b = CFG["budgets"]
    assert stop_reason({**OK, "steps": 3}, CFG) is None
    assert "step budget" in stop_reason({**OK, "steps": b["max_graph_steps"]}, CFG)
    assert "token budget" in stop_reason({**OK, "usage": {"tokens_in": b["max_tokens_per_job"], "tokens_out": 1}}, CFG)
    assert "token budget" not in (stop_reason({**OK, "usage": {"tokens_in": b["max_tokens_per_job"]}}, CFG) or "")  # at the limit is allowed
    assert "risk_assessor produced no result" in stop_reason({"job_spec": {"x": 1}, "fit_report": {"x": 1}}, CFG)
    err = stop_reason({"job_spec": {"x": 1}, "risk_report": {"x": 1}, "errors": ["fit_matcher: KeyError: boom"]}, CFG)
    assert "fit_matcher produced no result" in err and "boom" in err
    blocked = {**OK, "compliance_verdict": {"allowed": False, "violations": [{"rule_id": "P3-guarantees", "severity": "block"}]}}
    assert "P3-guarantees" in stop_reason(blocked, CFG)
    assert stop_reason({**OK, "steps": 99, "compliance_verdict": {"allowed": False, "violations": []}}, CFG).startswith("step budget")  # steps first


def test_conversation_stop_reason():
    b = CFG["budgets"]
    assert conversation_stop_reason({"conversation": {"intent": "question"}, "steps": 2}, CFG) is None
    assert "step budget" in conversation_stop_reason({"conversation": {"x": 1}, "steps": b["max_graph_steps"]}, CFG)
    assert "token budget" in conversation_stop_reason({"conversation": {"x": 1}, "usage": {"tokens_out": b["max_tokens_per_job"] + 1}}, CFG)
    assert "client_liaison produced no result" in conversation_stop_reason({"errors": ["client_liaison: ValueError: x"]}, CFG)


async def test_step_budget_dead_letters_before_any_expensive_node_runs():
    cfg = {**CFG, "budgets": {**CFG["budgets"], "max_graph_steps": 4}}  # intake, guard, 3 branches, join = 6 steps > 4
    out = await build_bid_graph(deps(cfg), MemorySaver()).ainvoke({"job": job()}, tid("b1"))
    assert out["needs_attention"] is True and out["dead_letter"]["reason"].startswith("step budget exhausted")
    nodes = [e["node"] for e in out["events"]]
    assert nodes[-1] == "dead_letter" and "writer" not in nodes and "pricing" not in nodes and "__interrupt__" not in out
    assert out["dead_letter"]["errors"] == [] and "job" in out["dead_letter"]["state_keys"]


async def test_critic_loop_is_capped_and_the_human_is_told_it_never_passed():
    impossible = {**CFG["critic"], "min_personalisation": 1.1}  # a measured criterion that no draft can satisfy
    cfg = {**CFG, "critic": impossible}
    out = await build_bid_graph(deps(cfg), MemorySaver()).ainvoke({"job": job()}, tid("b2"))
    nodes = [e["node"] for e in out["events"]]
    cap = CFG["budgets"]["max_revisions"]
    assert nodes.count("writer") == cap + 1 and nodes.count("critic") == cap + 1 and out["revision"] == cap
    intr = out["__interrupt__"][0].value
    assert intr["kind"] == "proposal_approval" and intr["needs_attention"] is True and intr["critique"]["passed"] is False
    assert "submission" not in out  # still needs the human, who sees the failed critique
