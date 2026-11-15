"""Alembic environment: online migrations on a sync engine; tests may hand in an open connection via config.attributes['connection']."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from agent.settings import get_settings
from apps.api.db import Base
from apps.api.migrate import sync_url

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=connection.dialect.name == "sqlite")  # SQLite cannot ALTER columns in place
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = config.get_main_option("sqlalchemy.url") or get_settings().app_database_url
    engine = create_engine(sync_url(url), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) mode is not used: run `make migrate` against a database")
run_migrations_online()
