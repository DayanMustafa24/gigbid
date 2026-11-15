"""Settings (env) + config (agent/config.yaml) loaders. Models are chosen here, never in node code."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("GIGBID_CONFIG", ROOT / "agent" / "config.yaml"))


def load_env(path: Path | str | None = None) -> bool:
    """Export `.env` into os.environ WITHOUT overriding real environment variables.

    pydantic-settings reads .env into `Settings` fields only; provider keys (GROQ_API_KEY, MCP_BEARER_TOKEN, ...) are resolved from
    os.environ by node_model()/default_llm()/prompt guard, so without this call they are invisible and /health/ready reports
    models + injection_classifier as unavailable. Called by the API container and the worker at startup (not at import: tests stay hermetic).
    """
    from dotenv import load_dotenv

    return bool(load_dotenv(path or ROOT / ".env", override=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "dev-only-change-me"
    token_encryption_key: str = ""
    database_url: str = "postgresql://gigbid:gigbid@localhost:5432/gigbid"
    redis_url: str = "redis://localhost:6379/0"
    marketplace_mode: str | None = None
    gigsim_base_url: str = "http://localhost:8081"
    gigsim_api_token: str = ""
    model_profile: str | None = None
    # ---- API (Phase 6) ----
    app_database_url: str = "sqlite+aiosqlite:///data/app/gigbid.sqlite"  # users/runs/events; postgresql+asyncpg://... in production
    reviewer_email: str = ""  # a login seeded at startup (deployment: the reviewer account); empty = none
    reviewer_password: str = ""
    session_ttl_minutes: int = 480
    cookie_secure: bool = False  # True behind HTTPS (deployment)
    allow_registration: bool = False  # the first account can always register; later ones only when this is true
    cors_origins: str = "http://localhost:3000"  # comma-separated; empty in production (same-origin proxy)
    api_runs_per_minute: int = 30  # per user: starting/deciding runs


class NodeModel(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key: str | None
    fallback: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def active_profile(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return get_settings().model_profile or cfg["models"]["profile"]


def marketplace_mode(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return get_settings().marketplace_mode or cfg["marketplace"]["mode"]


def node_model(node: str, cfg: dict[str, Any] | None = None) -> NodeModel:
    """Resolve node -> provider/model/base_url/api_key for the active profile."""
    cfg = cfg or load_config()
    profile = cfg["models"]["profiles"][active_profile(cfg)]
    spec = profile["nodes"].get(node)
    if spec is None:
        raise KeyError(f"node '{node}' has no model in profile '{active_profile(cfg)}'")
    prov = profile["providers"][spec["provider"]]
    base_url = prov.get("base_url") or os.environ.get(
        prov.get("base_url_env", ""), prov.get("default_base_url", "")
    )
    key_env = prov.get("api_key_env")
    return NodeModel(
        provider=spec["provider"],
        model=spec["model"],
        base_url=base_url,
        api_key=os.environ.get(key_env) if key_env else None,
        fallback=spec.get("fallback"),
    )
