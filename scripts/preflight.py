"""Pre-flight for `docker compose up` / deployment: checks .env WITHOUT printing any secret value.
Run: uv run python -m scripts.preflight   (exit 1 when something blocks a production start)
"""
from __future__ import annotations

import base64
import sys

from dotenv import dotenv_values

from agent.settings import ROOT

WEAK = {"", "replace_me", "dev-only-change-me", "devtoken", "changeme"}


def fernet_ok(key: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(key.encode())) == 32
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    env = dotenv_values(ROOT / ".env")
    if not (ROOT / ".env").exists():
        print("[FAIL] .env not found (copy .env.example)")
        return 1
    bad = warn = 0

    def need(name: str, ok: bool, why: str) -> None:
        nonlocal bad
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f"  -> {why}"))
        bad += 0 if ok else 1

    def want(name: str, ok: bool, why: str) -> None:
        nonlocal warn
        print(f"[{'ok' if ok else 'warn'}] {name}" + ("" if ok else f"  -> {why}"))
        warn += 0 if ok else 1

    g = lambda k: (env.get(k) or "").strip()  # noqa: E731
    need("APP_SECRET_KEY", g("APP_SECRET_KEY") not in WEAK and len(g("APP_SECRET_KEY")) >= 24, "random, >= 24 chars (python -c \"import secrets;print(secrets.token_urlsafe(32))\")")
    need("TOKEN_ENCRYPTION_KEY", fernet_ok(g("TOKEN_ENCRYPTION_KEY")), "Fernet key: python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\"")
    need("MCP_BEARER_TOKEN", g("MCP_BEARER_TOKEN") not in WEAK and len(g("MCP_BEARER_TOKEN")) >= 16, "random, >= 16 chars (servers refuse the default in production)")
    need("REVIEWER_EMAIL / REVIEWER_PASSWORD", "@" in g("REVIEWER_EMAIL") and len(g("REVIEWER_PASSWORD")) >= 10, "reviewer login for the public URL (password >= 10 chars)")
    want("GROQ_API_KEY", bool(g("GROQ_API_KEY")), "empty = nodes run in deterministic template mode (no LLM drafts)")
    want("GIGSIM_API_TOKEN", g("GIGSIM_API_TOKEN") not in WEAK, "empty/placeholder = GigSim runs without auth (fine on the private compose network)")
    want("POSTGRES_PASSWORD", g("POSTGRES_PASSWORD") not in ("", "gigbid"), "compose default 'gigbid' is fine locally, change it on a public host")
    want("COOKIE_SECURE", g("COOKIE_SECURE").lower() == "true", "set true when served over HTTPS (public deployment)")
    print(f"\n{bad} blocking, {warn} warning(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
