"""Check that keys/services in .env work. Run: uv run python -m scripts.check_env  (never prints secrets)."""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()


def show(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        show("groq", False, "(GROQ_API_KEY empty)")
    else:
        try:
            r = httpx.get("https://api.groq.com/openai/v1/models", headers=bearer(key), timeout=20)
            ids = sorted(m["id"] for m in r.json().get("data", [])) if r.status_code == 200 else []
            show("groq", r.status_code == 200, f"status={r.status_code} models={len(ids)}")
            for m in ids:
                print("      ", m)
        except Exception as e:  # noqa: BLE001
            show("groq", False, type(e).__name__)

    gh = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not gh:
        show("github", False, "(token empty)")
    else:
        try:
            r = httpx.get("https://api.github.com/user", headers=bearer(gh), timeout=20)
            show("github", r.status_code == 200, f"status={r.status_code} login={r.json().get('login')}")
        except Exception as e:  # noqa: BLE001
            show("github", False, type(e).__name__)

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").removesuffix("/v1")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=5)
        names = [m["name"] for m in r.json().get("models", [])]
        show("ollama", r.status_code == 200, f"models={names}")
    except Exception as e:  # noqa: BLE001
        show("ollama", False, f"not reachable ({type(e).__name__})")


if __name__ == "__main__":
    main()
