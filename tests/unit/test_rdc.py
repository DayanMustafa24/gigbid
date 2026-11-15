"""Phase 5.9 tests: the R&D-C harness (single agent vs supervisor vs hand graph). Offline, scripted LLMs: these prove the PLUMBING and the
scoring maths, not which architecture is better (that needs the real-model run, evals/results/rdc_architectures.json)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver

from agent.graphs.bid_graph import build_bid_graph
from agent.llm import LLMRouter, ScriptedBackend
from agent.nodes.common import Deps
from agent.settings import load_config
from evals.runners.rag_eval import PERSONAS
from evals.runners.rdc_architectures import (
    RunResult,
    evaluate,
    merge_state,
    run_hand_graph,
    run_single_agent,
    run_supervisor,
    sample_jobs,
    score_one,
    scripted_llm,
    state_summary,
    summarise,
)
from rag.types import Item, Retrieval

CFG = load_config()
PROFILE = {"display_name": "Priya", "hourly_rate_usd": 55, "minimum_project_budget_usd": 300, "years_experience": 6, "skills": ["RAG"]}
LETTER = "I built a RAG system with pgvector for a legal client. I increased revenue by 400% for Google."


def job(**kw):
    base = {"job_id": "j1", "client_id": "c1", "title": "RAG chatbot", "description": "Need a RAG chatbot built with FastAPI.",
            "required_skills": ["RAG", "FastAPI"], "screening_questions": ["Why you?"],
            "budget": {"min": 100, "max": 500, "type": "fixed", "currency": "USD"}, "connects_cost": 8, "category": "AI Chatbot Development",
            "gold": {"action": "bid", "scam": False, "injection": False}}
    return {**base, **kw}


class Retr:
    def retrieve(self, q, k):
        return Retrieval(evidence_ids=["e1"], projects=["proj1"])


def deps():
    e1 = Item("e1", "p", "case_study", "proj1", "Built RAG with pgvector and FastAPI for a legal client")
    return Deps(retriever=Retr(), corpus=SimpleNamespace(by_id={"e1": e1}, by_project={"proj1": [e1]}), profile=PROFILE, persona="ai_ml")


def router(fn):
    return LLMRouter(ScriptedBackend(fn), CFG)


def last_result(user):
    return json.loads(user.rsplit("<tool_result>", 1)[1].split("</tool_result>")[0])


def test_merge_state_follows_reducer_semantics():
    s = merge_state({"errors": ["a"], "steps": 1, "usage": {"tokens_in": 5}}, {"errors": ["b"], "steps": 2, "usage": {"tokens_in": 1}, "x": 1})
    assert s == {"errors": ["a", "b"], "steps": 3, "usage": {"tokens_in": 6}, "x": 1}


def test_state_summary_is_compact_and_hides_untrusted_text():
    st = {"events": [{"node": "risk_assessor"}], "risk_report": {"score": 0.2, "flags": []}, "fit_report": {"score": 0.7, "matched_skills": ["RAG"]},
          "proposal_draft": {"cover_letter": "one two three"}, "critique": {"passed": False, "issues": ["a", "b", "c", "d"]}}
    s = state_summary(st)
    assert s["done"] == ["risk_assessor"] and s["draft_words"] == 3 and s["critique"]["issues"] == ["a", "b", "c"] and "job" not in s


async def test_single_agent_verifies_then_finishes():
    def fn(nm, system, user):
        n = user.count("<tool_result>")
        if n == 0:
            return json.dumps({"action": "tool", "tool": "risk_check", "args": {}})
        if n == 1:
            return json.dumps({"action": "tool", "tool": "search_evidence", "args": {"query": "rag"}})
        if n == 2:
            return json.dumps({"action": "tool", "tool": "verify_draft", "args": {"cover_letter": LETTER}})
        return json.dumps({"action": "final", "decision": "bid", "price": 450, "cover_letter": last_result(user)["cleaned_letter"]})

    r = await run_single_agent(job(), deps(), router(fn))
    assert r.decision == "bid" and r.verified and r.steps == 4 and r.llm_calls == 4 and r.tokens == 60 and not r.errors and not r.limit_hit
    assert r.raw_draft == LETTER and "400%" not in r.draft and "pgvector" in r.draft


async def test_single_agent_can_skip_verification_and_the_harness_sees_it():
    r = await run_single_agent(job(), deps(), router(lambda nm, s, u: json.dumps({"action": "final", "decision": "bid", "cover_letter": LETTER})))
    assert r.decision == "bid" and r.verified is False and r.raw_draft == LETTER


async def test_single_agent_step_limit_and_bad_output_are_failures_not_crashes():
    loop = await run_single_agent(job(), deps(), router(lambda nm, s, u: json.dumps({"action": "tool", "tool": "risk_check", "args": {}})))
    assert loop.limit_hit and loop.decision is None and loop.steps == 12
    bad = await run_single_agent(job(), deps(), router(lambda nm, s, u: "not json"))
    assert bad.decision is None and len(bad.errors) == 1 and bad.errors[0].startswith("single_agent: StructuredOutputError")


async def test_supervisor_runs_workers_in_llm_order_and_verifies_after_the_last_draft():
    r = await run_supervisor(job(), deps(), router(scripted_llm))
    assert r.decision == "bid" and r.verified and r.raw_draft and r.draft and r.steps > 8 and not r.limit_hit
    skipper = await run_supervisor(job(), deps(), router(lambda nm, s, u: json.dumps({"next": "FINISH", "decision": "skip"})))
    assert skipper.decision == "skip" and skipper.verified is False and skipper.draft == ""
    unknown = await run_supervisor(job(), deps(), router(lambda nm, s, u: json.dumps({"next": "wizard"})))
    assert unknown.limit_hit and unknown.decision is None and any("unknown worker" in e for e in unknown.errors)


async def test_hand_graph_always_verifies():
    d = deps()
    r = await run_hand_graph(job(), d, build_bid_graph(d, MemorySaver()))
    assert r.decision == "bid" and r.verified and r.raw_counts is not None and r.draft and r.steps < 30


def test_score_one_uses_one_checker_for_raw_and_final():
    d = deps()
    row = score_one(RunResult("x", "j1", decision="bid", draft="I built a RAG system with pgvector for a legal client.", raw_counts=(1, 3), verified=True), job(), d)
    assert row["pred"] == "bid" and row["raw_unsupported"] == 1 and row["final_unsupported"] == 0 and row["policy_violation"] is False
    raw = score_one(RunResult("x", "j1", decision="bid", draft="I guarantee a perfect result.", raw_draft=LETTER), job(), d)
    assert raw["policy_violation"] is True and raw["raw_unsupported"] == 1 and raw["verified"] is False
    skip = score_one(RunResult("x", "j1", decision="watch"), job(gold={"action": "skip", "scam": False, "injection": False}), d)
    assert skip["pred"] == "skip" and "final_unsupported" not in skip and not skip["failed"]
    assert score_one(RunResult("x", "j1"), job(), d)["failed"] is True


def test_summarise_metrics():
    def row(gold, pred, scam=False, failed=False):
        return {"gold": gold, "pred": pred, "scam": scam, "injection": False, "verified": False, "steps": 4, "tokens": 100, "latency_s": 2.0,
                "failed": failed, "errors": 0}

    s = summarise([row("bid", "bid"), row("skip", "skip", scam=True), row("skip", "bid", scam=True), row("bid", None, failed=True)])
    assert s["n_jobs"] == 4 and s["decision_accuracy"] == 0.5 and s["bid_precision"] == 0.5 and s["bid_recall"] == 0.5 and s["bid_skip_f1"] == 0.5
    assert s["scam_bid_rate"] == 0.5 and s["failure_rate"] == 0.25 and s["steps_mean"] == 4.0 and s["raw_unsupported_per_proposal"] is None
    assert summarise([])["n_jobs"] == 0


def test_sample_is_seeded_and_balanced():
    a, b = sample_jobs(list(PERSONAS), 6, 1), sample_jobs(list(PERSONAS), 6, 1)
    assert [j["job_id"] for _, j in a] == [j["job_id"] for _, j in b] and len(a) == 6
    assert sorted(p for p, _ in a) == sorted(list(PERSONAS) * 2)
    assert [j["job_id"] for _, j in sample_jobs(list(PERSONAS), 6, 2)] != [j["job_id"] for _, j in a]


async def test_offline_end_to_end_runs_all_three_on_real_dev_jobs():
    jobs = sample_jobs(["ai_ml"], 3, 7)
    out = await evaluate(["hand_graph", "supervisor", "single_agent"], jobs, router(scripted_llm), CFG)
    assert set(out) == {"hand_graph", "supervisor", "single_agent"}
    for arch, v in out.items():
        assert v["overall"]["n_jobs"] == 3 and len(v["rows"]) == 3, arch
