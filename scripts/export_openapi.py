"""Write the API's OpenAPI schema (the contract the web app's TypeScript client is generated from).

    uv run python -m scripts.export_openapi            # writes apps/api/openapi.json
    uv run python -m scripts.export_openapi --check    # exit 1 if the committed file is stale (CI)

No server, database or network needed: the schema is built from the route and model definitions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "apps" / "api" / "openapi.json"


def render() -> str:
    from apps.api.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    text = render()
    if a.check:
        if not a.out.exists() or a.out.read_text(encoding="utf-8") != text:
            print(f"{a.out} is stale: run `make openapi`", file=sys.stderr)
            return 1
        print("openapi.json is up to date")
        return 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {a.out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
