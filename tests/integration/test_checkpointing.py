"""Phase 5.6 tests: durable checkpoints. A run paused at the human approval gate survives a process restart (new saver, same store),
resumes once, and a replay from an old checkpoint cannot double-submit because idempotency keys are derived from the content.

The same test runs on SQLite (always) and on Postgres (only when you opt in: set GIGBID_TEST_POSTGRES=1 and DATABASE_URL points at a
reachable database, e.g. `docker compose up postgres`). Postgres is skipped by default because it needs a running server."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from agent.checkpointing import open_checkpointer
from agent.graphs.bid_graph import build_bid_graph
from agent.marketplace_port import ClientHistory, PlatformPolicy, Proposal, SubmissionPreview, SubmissionReceipt
from agent.nodes.common import Deps
from rag.types import Item, Retrieval

PROFILE = {"display_name": "Priya", "hourly_rate_usd": 55, "minimum_project_budget_usd": 300, "years_experience": 6, "skills": ["RAG"]}
NO_PG = os.environ.get("GIGBID_TEST_POSTGRES") != "1"
KINDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(NO_PG, reason="set GIGBID_TEST_POSTGRES=1 with a reachable DATABASE_URL"))]


def job():
    return {"job_id": "j1", "client_id": "c1", "title": "RAG chatbot", "description": "Need a RAG chatbot built with FastAPI.",
            "required_skills": ["RAG", "FastAPI"], "screening_questions": ["Why you?"],
            "budget": {"min": 100, "max": 500, "type": "fixed", "currency": "USD"}, "connects_cost": 8, "category": "AI Chatbot Development"}


class Retr:
    def retrieve(self, q, k):
        return Retrieval(evidence_ids=["e1"], projects=["proj1"])


class FakeMarket:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def get_client_history(self, cid):
        return ClientHistory(client_id=cid, payment_verified=True, completed_projects=5, average_rating=4.8, member_since_days=400)

    async def get_platform_policy(self):
        return PlatformPolicy(version="2026-11-02", update_active=True, connects_surcharge=2, requires_ai_disclosure=True)

    async def create_proposal_draft(self, draft, idempotency_key):
        self.calls.append(("create", idempotency_key))
        return Proposal(proposal_id="p1", job_id=draft.job_id, status="draft")

    async def preview_submission(self, pid):
        return SubmissionPreview(proposal_id=pid, would_spend=10, balance_before=100, balance_after=90, submittable=True)

    async def submit_proposal(self, pid, idempotency_key):
        self.calls.append(("submit", idempotency_key))
        return SubmissionReceipt(proposal_id=pid, status="submitted", connects_spent=10, balance_after=90)


def deps(market):
    e1 = Item("e1", "p", "case_study", "proj1", "Built RAG with pgvector and FastAPI for a legal client")
    return Deps(retriever=Retr(), corpus=SimpleNamespace(by_id={"e1": e1}, by_project={"proj1": [e1]}), profile=PROFILE, persona="ai_ml",
                market=market)


@pytest.mark.parametrize("kind", KINDS)
async def test_paused_run_survives_restart_resumes_once_and_replay_is_idempotent(tmp_path, kind):
    kw = {"path": tmp_path / "graph.sqlite"} if kind == "sqlite" else {}
    cfg = {"configurable": {"thread_id": f"durable-{uuid.uuid4().hex[:8]}"}}  # unique: a shared Postgres must not see old threads
    m = FakeMarket()
    async with open_checkpointer(kind, **kw) as cp:
        out = await build_bid_graph(deps(m), cp).ainvoke({"job": job()}, cfg)
        assert out["__interrupt__"][0].value["kind"] == "proposal_approval" and m.calls == []
    # "process restart": a brand-new saver and graph object on the same store
    async with open_checkpointer(kind, **kw) as cp:
        g = build_bid_graph(deps(m), cp)
        paused = await g.aget_state(cfg)
        assert paused.next == ("approval_gate",) and paused.values["proposal_draft"]["cover_letter"]
        done = await g.ainvoke(Command(resume={"status": "approve"}), cfg)
        assert done["approval_status"] == "submitted" and [c[0] for c in m.calls] == ["create", "submit"]
        assert (await g.aget_state(cfg)).next == ()
        # replay from the checkpoint taken at the gate: later nodes run again, but with the SAME keys (the marketplace dedupes them)
        history = [s async for s in g.aget_state_history(cfg)]
        at_gate = [s for s in history if s.next == ("approval_gate",)]
        assert len(at_gate) == 1 and len(history) > 5
        await g.ainvoke(Command(resume={"status": "approve"}), at_gate[0].config)
        assert [c[1] for c in m.calls[2:]] == [c[1] for c in m.calls[:2]]


async def test_factory_kinds_and_errors():
    async with open_checkpointer("memory") as cp:
        assert type(cp).__name__ in ("MemorySaver", "InMemorySaver")
    with pytest.raises(ValueError, match="unknown checkpointer kind"):
        async with open_checkpointer("redis"):
            pass
