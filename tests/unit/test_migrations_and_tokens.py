"""Alembic migrations match the models (drift fails CI) + the encrypted OAuth token store."""
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, select

from apps.api.auth import TokenStore, TokenStoreError
from apps.api.auth.token_store import make_fernet
from apps.api.db import Base, OAuthToken, create_all, make_engine, make_sessionmaker
from apps.api.migrate import downgrade, sync_url, upgrade


def test_sync_url_mapping():
    assert sync_url("sqlite+aiosqlite:///:memory:") == "sqlite://"
    assert sync_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sync_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sync_url("sqlite+aiosqlite:///C:/x/app.sqlite").startswith("sqlite:///") and "aiosqlite" not in sync_url("sqlite+aiosqlite:///C:/x/app.sqlite")


def test_migrations_create_exactly_the_model_schema(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.sqlite').as_posix()}"
    upgrade(url)
    eng = create_engine(url)
    assert {"users", "runs", "run_events", "user_settings", "oauth_tokens", "alembic_version"} <= set(inspect(eng).get_table_names())
    with eng.connect() as conn:
        drift = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert drift == [], f"models and migrations disagree: add a migration (alembic revision --autogenerate): {drift}"
    downgrade(url)
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}


async def test_token_store_encrypts_scopes_and_detects_wrong_key(tmp_path):
    eng = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.sqlite').as_posix()}")
    await create_all(eng)
    sm = make_sessionmaker(eng)
    store = TokenStore(sm, make_fernet(None, "dev-secret-0123456789", "development"))
    await store.put("owner-1", "upwork", {"access_token": "AAA-very-secret", "refresh_token": "R1"})
    assert (await store.get("owner-1", "upwork"))["access_token"] == "AAA-very-secret"
    assert await store.get("owner-2", "upwork") is None                     # tenant scoping
    async with sm() as db:
        raw = (await db.execute(select(OAuthToken.ciphertext))).scalar_one()
    assert "AAA-very-secret" not in raw                                      # only ciphertext at rest
    await store.put("owner-1", "upwork", {"access_token": "BBB"})            # upsert
    assert (await store.get("owner-1", "upwork"))["access_token"] == "BBB"
    other = TokenStore(sm, make_fernet(Fernet.generate_key().decode(), "x", "development"))
    with pytest.raises(TokenStoreError):
        await other.get("owner-1", "upwork")
    assert await store.delete("owner-1", "upwork") and not await store.delete("owner-1", "upwork")
    await eng.dispose()


def test_production_requires_an_explicit_key():
    with pytest.raises(TokenStoreError):
        make_fernet(None, "secret", "production")
    with pytest.raises(TokenStoreError):
        make_fernet("not-a-fernet-key", "secret", "development")
