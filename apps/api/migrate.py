"""Alembic helpers. CLI:  uv run python -m apps.api.migrate [upgrade|current|history]   (also `make migrate`).

Migrations run on a SYNC engine (sqlite / postgresql+psycopg) even though the app is async: simpler and equally correct for DDL.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from agent.settings import ROOT, get_settings


def sync_url(url: str) -> str:
    """App URL (async drivers, repo-relative SQLite path) -> URL for the synchronous engine Alembic uses."""
    if url.startswith("sqlite"):
        if ":memory:" in url:
            return "sqlite://"
        raw = url.split("///", 1)[1]
        p = Path(raw) if Path(raw).is_absolute() else ROOT / raw
        p.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{p.as_posix()}"
    url = url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps" / "api" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sync_url(url or get_settings().app_database_url).replace("%", "%%"))
    return cfg


def upgrade(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["upgrade", "current", "history"], nargs="?", default="upgrade")
    a = ap.parse_args()
    cfg = alembic_config()
    {"upgrade": lambda: command.upgrade(cfg, "head"), "current": lambda: command.current(cfg, verbose=True),
     "history": lambda: command.history(cfg)}[a.action]()


if __name__ == "__main__":
    main()
