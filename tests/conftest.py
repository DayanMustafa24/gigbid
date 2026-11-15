"""Shared fixtures. `api_harness` builds the real FastAPI app on a temp SQLite file with the real bid graph, a memory checkpointer and
offline fakes (no network, no model key): the same wiring production uses, minus the outside world."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent.memory.store import MemoryStore
from agent.nodes.common import Deps
from agent.settings import Settings
from apps.api.container import Container
from apps.api.main import create_app
from rag.types import Item, Retrieval

PROFILE = {"display_name": "Priya", "hourly_rate_usd": 55, "minimum_project_budget_usd": 300, "years_experience": 6, "skills": ["RAG"]}
PASSWORD = "correct horse battery"


class _Retr:
    def retrieve(self, q: Any, k: Any) -> Retrieval:
        return Retrieval(evidence_ids=["e1"], projects=["proj1"])


def _corpus() -> SimpleNamespace:
    e1 = Item("e1", "p", "case_study", "proj1", "Built RAG with pgvector and FastAPI for a legal client")
    return SimpleNamespace(by_id={"e1": e1}, by_project={"proj1": [e1]})


def offline_deps(_persona: str) -> Deps:
    return Deps(retriever=_Retr(), corpus=_corpus(), profile=PROFILE, price_stats={}, persona="ai_ml")


class FakeMarket:
    """Just enough of MarketplacePort for the API/worker: policy (status check), search, get_job. `down=True` simulates an outage."""

    def __init__(self, jobs: list[dict[str, Any]] | None = None, down: bool = False) -> None:
        self.jobs = {j["job_id"]: j for j in (jobs or [])}
        self.down, self.closed = down, False

    async def get_platform_policy(self) -> Any:
        if self.down:
            raise RuntimeError("down")
        return SimpleNamespace(version="test", model_dump=lambda: {"version": "test"})

    async def get_connects_balance(self) -> Any:
        return SimpleNamespace(balance=100)

    async def search_jobs(self, q: str, limit: int = 10, offset: int = 0) -> list[Any]:
        return [SimpleNamespace(job_id=j["job_id"], title=j["title"]) for j in list(self.jobs.values())[:limit]]

    async def get_job(self, job_id: str) -> Any:
        return SimpleNamespace(model_dump=lambda: dict(self.jobs[job_id]))

    async def aclose(self) -> None:
        self.closed = True


def market_job(job_id: str = "j1") -> dict[str, Any]:
    return {"job_id": job_id, "client_id": "", "title": "RAG chatbot", "description": "Need a RAG chatbot built with FastAPI.",
            "required_skills": ["RAG", "FastAPI"], "screening_questions": [], "duration_days": None, "category": None, "connects_cost": 8,
            "budget": {"min": 100, "max": 500, "type": "fixed", "currency": "USD"}}


INLINE_JOB = {"title": "RAG chatbot", "description": "Need a RAG chatbot built with FastAPI.", "budget_min": 100, "budget_max": 500,
              "required_skills": ["RAG", "FastAPI"]}


@pytest.fixture
async def api_harness(tmp_path):
    built: list[tuple[Container, MemoryStore, list[httpx.AsyncClient]]] = []

    async def make(*, market: Any = None, allow_registration: bool = False, name: str = "app", market_in_graph: bool = False,
                   **settings: Any) -> SimpleNamespace:
        """`market_in_graph=True` also hands the marketplace to the graph's Deps (the production wiring), so the executor can write."""
        s = Settings(app_env="test", app_secret_key="test-secret-key-0123456789abcdef0123", allow_registration=allow_registration,
                     reviewer_email="", reviewer_password="", api_runs_per_minute=10_000, **settings)
        mem = MemoryStore(":memory:")
        factory = (lambda persona: dataclasses.replace(offline_deps(persona), market=market)) if market_in_graph else offline_deps
        c = Container(s, db_url=f"sqlite+aiosqlite:///{(tmp_path / (name + '.sqlite')).as_posix()}", memory=mem, market=market, llm=None,
                      classifier=None, deps_factory=factory, checkpointer_kind="memory", slots=lambda: [])
        await c.startup()
        app = create_app(c)
        clients: list[httpx.AsyncClient] = []

        def client() -> httpx.AsyncClient:  # one cookie jar per client = one signed-in user
            cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            clients.append(cl)
            return cl

        built.append((c, mem, clients))
        return SimpleNamespace(c=c, app=app, svc=app.state.service, client=client)

    yield make
    for c, mem, clients in built:
        for cl in clients:
            await cl.aclose()
        await c.shutdown()
        mem.close()
