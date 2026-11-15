"""Application database (users, runs, run events, per-user settings). SQLAlchemy 2 async.

Default is a local SQLite file (zero services, works on the owner's machine today); production points APP_DATABASE_URL at Postgres
(`postgresql+psycopg://...`; psycopg 3 is already a dependency and supports async). Same models, same code. Every row that belongs to a person carries `owner_id`
and every query filters on it (tenant scoping). Graph checkpoints live elsewhere (agent/checkpointing.py); long-term memory in
agent/memory. Schema changes go through Alembic (apps/api/migrations, `make migrate`); `create_all` is only the zero-config dev/test bootstrap
(a no-op once migrations created the tables). A test fails if the models and the migrations drift apart.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent.settings import ROOT


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | reviewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    """One graph execution (a bid for a job, or a reply to a client message). `thread_id` keys the checkpoint."""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # bid | conversation
    subject_id: Mapped[str] = mapped_column(String(120))  # job_id or proposal_id
    persona: Mapped[str] = mapped_column(String(60))
    thread_id: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    # running | awaiting_approval | completed | skipped | closed | needs_attention | failed
    decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # the interrupt payload shown to the human
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RunEvent(Base):
    """Execution trace rows (one per node run). Replayed by the SSE endpoint so a reconnecting client misses nothing."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    node: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class UserSetting(Base):
    __tablename__ = "user_settings"
    owner_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OAuthToken(Base):
    """Encrypted OAuth credentials per (owner, provider), e.g. the Upwork MCP grant (Phase 3.5). Only ciphertext is stored."""

    __tablename__ = "oauth_tokens"
    owner_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite") and ":memory:" not in url:
        raw = url.split("///", 1)[1]
        p = Path(raw) if Path(raw).is_absolute() else ROOT / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{p.as_posix()}"
    return create_async_engine(url, future=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
