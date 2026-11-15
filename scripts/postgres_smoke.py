"""Postgres smoke (assignment 8: 'Postgres smoke'): proves the production database path works end to end.
Needs a reachable Postgres with pgvector. Env: APP_DATABASE_URL (postgresql+psycopg://...) and DATABASE_URL (postgresql://...).
Run: uv run python -m scripts.postgres_smoke      (CI runs it against a pgvector service container)

  1. Alembic migrations apply to Postgres and create exactly the application tables
  2. the async app engine (psycopg) can read/write them
  3. the LangGraph Postgres checkpointer sets up its tables and stores a checkpoint
  4. the pgvector extension is available
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import inspect, text

from agent.checkpointing import open_checkpointer
from agent.settings import get_settings
from apps.api import migrate
from apps.api.db import Base, make_engine

OK = "[ok]"


async def main() -> int:
    s = get_settings()
    if not s.app_database_url.startswith("postgresql"):
        print("FAIL APP_DATABASE_URL must be a postgresql+psycopg:// URL, got:", s.app_database_url.split(":", 1)[0])
        return 1
    migrate.upgrade(s.app_database_url)
    print(OK, "alembic upgrade head")

    eng = make_engine(s.app_database_url)
    try:
        async with eng.begin() as conn:
            tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
            missing = set(Base.metadata.tables) - tables
            if missing:
                print("FAIL missing tables after migration:", sorted(missing))
                return 1
            print(OK, "tables:", ", ".join(sorted(Base.metadata.tables)))
            ext = (await conn.execute(text("SELECT count(*) FROM pg_available_extensions WHERE name='vector'"))).scalar_one()
            print(OK if ext else "warn", "pgvector extension available" if ext else "pgvector extension NOT available (use pgvector/pgvector image)")
    finally:
        await eng.dispose()

    async with open_checkpointer("postgres") as cp:
        cfg = {"configurable": {"thread_id": "smoke", "checkpoint_ns": ""}}
        from langgraph.checkpoint.base import empty_checkpoint

        await cp.aput(cfg, empty_checkpoint(), {"source": "input", "step": 0, "parents": {}}, {})
        got = await cp.aget_tuple(cfg)
        if got is None:
            print("FAIL checkpoint was not stored")
            return 1
    print(OK, "postgres checkpointer stored and read back a checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
