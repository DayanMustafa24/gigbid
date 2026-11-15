"""Application container: owns every long-lived resource of the API process (database, graph checkpointer, long-term memory, marketplace,
model router, injection classifier, event broker) and hands out per-run `Deps`. Created in the FastAPI lifespan, closed on shutdown.

Tests pass fakes (`deps_factory`, `market`, `llm=None`, in-memory paths) so the whole API runs offline. Everything optional degrades:
no model key -> deterministic node modes; no marketplace -> jobs endpoints answer 503 `marketplace_unavailable` (the UI banner), not a crash.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from agent.checkpointing import open_checkpointer
from agent.memory.store import MemoryStore
from agent.nodes.common import Deps
from agent.settings import Settings, get_settings, load_config, load_env

from .auth import LoginThrottle, check_secret, hash_password
from .db import User, create_all, make_engine, make_sessionmaker

log = logging.getLogger("gigbid.api")
AUTO = object()  # "build the default": distinguishes it from an explicit None ("none, on purpose")


class EventBroker:
    """In-process wake-up signal for SSE clients. The source of truth is the run_events table; a queue item only says 'look again'.
    Multi-instance deployments swap this for Redis pub/sub without touching callers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[str]) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, run_id: str, signal: str = "event") -> None:
        for q in list(self._subs.get(run_id, ())):
            try:
                q.put_nowait(signal)
            except asyncio.QueueFull:  # a slow client catches up from the database on its next wake-up
                pass


class Container:
    def __init__(self, settings: Settings | None = None, cfg: dict[str, Any] | None = None, *, db_url: str | None = None,
                 memory: MemoryStore | None = None, market: Any = AUTO, llm: Any = AUTO, classifier: Any = AUTO,
                 deps_factory: Callable[[str], Deps] | None = None, checkpointer_kind: str | None = None,
                 checkpointer_path: str | None = None, slots: Callable[[], Any] | None = None) -> None:
        self.settings = settings or get_settings()
        self.cfg = cfg or load_config()
        self.db_url = db_url or self.settings.app_database_url
        self._memory, self._market, self._llm, self._classifier = memory, market, llm, classifier
        self.deps_factory, self.slots = deps_factory, slots
        self.cp_kind, self.cp_path = checkpointer_kind, checkpointer_path
        self.broker = EventBroker()
        self.throttle = LoginThrottle()
        self.tasks: set[asyncio.Task[Any]] = set()
        self._deps_cache: dict[str, Deps] = {}
        self._deps_lock = asyncio.Lock()
        self.checkpointer: Any = None
        self._cp_cm: Any = None

    # ---- lifecycle ---------------------------------------------------------------------------------------------------------------
    async def startup(self) -> None:
        if self.settings.app_env != "test":
            load_env()  # provider keys / MCP token from .env -> os.environ (models, Prompt Guard, MCP client read them there)
        check_secret(self.settings.app_secret_key, self.settings.app_env)
        self.engine = make_engine(self.db_url)
        await create_all(self.engine)
        self.sessions = make_sessionmaker(self.engine)
        self.memory = self._memory or MemoryStore()
        if self._market is AUTO:
            try:
                from agent.mcp_marketplace import build_agent_marketplace

                self.market = build_agent_marketplace(self.cfg)
            except Exception as e:  # e.g. mode=upwork before the live adapter exists: run without a marketplace, say so in /health
                log.warning("marketplace unavailable: %s", e)
                self.market = None
        else:
            self.market = self._market
        if self._llm is AUTO:
            from agent.runtime import default_llm

            self.llm = default_llm(self.cfg)
        else:
            self.llm = self._llm
        if self._classifier is AUTO:
            from agent.security.prompt_guard import build_prompt_guard

            self.classifier = build_prompt_guard(self.cfg)
        else:
            self.classifier = self._classifier
        if self.slots is None:
            gateway = getattr(self.market, "gateway", None)  # MCP path: slots come from the Workspace MCP server like every other tool
            if gateway is not None:
                from agent.mcp_marketplace import workspace_slots_provider

                self.slots = workspace_slots_provider(gateway)
            else:
                self.slots = self._workspace_slots()
        self._cp_cm = open_checkpointer(self.cp_kind, self.cfg, self.cp_path)
        self.checkpointer = await self._cp_cm.__aenter__()
        await self.seed_reviewer()

    async def shutdown(self) -> None:
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self._cp_cm is not None:
            await self._cp_cm.__aexit__(None, None, None)
        if getattr(self, "market", None) is not None and hasattr(self.market, "aclose"):
            await self.market.aclose()
        if self._memory is None and getattr(self, "memory", None) is not None:
            self.memory.close()
        await self.engine.dispose()

    @staticmethod
    def _workspace_slots() -> Callable[[], Any]:
        """Free slots from the local mock calendar (documented stand-in); replaced by a Google Calendar adapter later."""
        def provider() -> Any:
            from mcp_servers.workspace.store import WorkspaceStore

            today = date.today()
            return WorkspaceStore().free_slots((today + timedelta(days=1)).isoformat(), (today + timedelta(days=7)).isoformat(), 30, 5)

        return provider

    async def seed_reviewer(self) -> None:
        s = self.settings
        if not (s.reviewer_email and s.reviewer_password):
            return
        async with self.sessions() as db:
            if (await db.execute(select(User).where(User.email == s.reviewer_email.lower()))).scalar_one_or_none() is None:
                db.add(User(email=s.reviewer_email.lower(), password_hash=hash_password(s.reviewer_password), role="reviewer"))
                await db.commit()

    # ---- per-run dependencies ---------------------------------------------------------------------------------------------------
    async def deps_for(self, persona: str, owner: str) -> Deps:
        """Shared, read-only parts (index, corpus) are built once per persona; owner and memory are set per run."""
        async with self._deps_lock:
            base = self._deps_cache.get(persona)
            if base is None:
                if self.deps_factory:
                    base = self.deps_factory(persona)
                else:
                    from agent.runtime import build_deps

                    base = await asyncio.to_thread(build_deps, persona, cfg=self.cfg, llm=self.llm, market=self.market, memory=self.memory,
                                                   classifier=self.classifier, slots=self.slots)
                self._deps_cache[persona] = base
        return dataclasses.replace(base, owner=owner, memory=self.memory)

    async def marketplace_status(self) -> str:
        if self.market is None:
            return "unconfigured"
        try:
            await asyncio.wait_for(self.market.get_platform_policy(), timeout=3)
            return "up"
        except Exception:
            return "down"
