"""Durable graph checkpoints (assignment 5.6): a paused run (human approval can take days) must survive a process restart and resume
exactly once. One factory, three backends chosen by config `checkpointer.kind` (env CHECKPOINTER overrides):

  memory    MemorySaver. Unit tests only: nothing survives the process.
  sqlite    AsyncSqliteSaver, one file. Local development and CI; VERIFIED (tests/integration/test_checkpointing.py).
  postgres  AsyncPostgresSaver on DATABASE_URL. Production / docker compose. NOT yet exercised: Docker could not be installed on the
            owner's machine; run `make up` + this module's smoke test when it can (docs/RUNBOOK.md).

    async with open_checkpointer() as cp:
        graph = build_bid_graph(deps, cp)

Exactly-once side effects do not depend on the checkpointer alone: the executor and the message sender derive idempotency keys from
(job or proposal, exact text), so even a replayed node cannot create or send twice.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent.settings import ROOT, get_settings, load_config


def checkpointer_kind(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return os.environ.get("CHECKPOINTER") or cfg.get("checkpointer", {}).get("kind", "sqlite")


@asynccontextmanager
async def open_checkpointer(kind: str | None = None, cfg: dict[str, Any] | None = None, path: str | Path | None = None) -> AsyncIterator[Any]:
    cfg = cfg or load_config()
    kind = kind or checkpointer_kind(cfg)
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        yield MemorySaver()
    elif kind == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        raw = Path(path) if path else Path(cfg.get("checkpointer", {}).get("sqlite_path", "data/checkpoints/graph.sqlite"))
        p = raw if raw.is_absolute() else ROOT / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(p)) as cp:
            yield cp
    elif kind == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(get_settings().database_url) as cp:
            await cp.setup()  # creates the checkpoint tables once; idempotent
            yield cp
    else:
        raise ValueError(f"unknown checkpointer kind {kind!r}; expected memory | sqlite | postgres")
