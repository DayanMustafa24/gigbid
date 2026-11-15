"""Generate the typed TypeScript API client from the FastAPI OpenAPI contract (assignment D: "do not hand-edit").

    uv run python -m scripts.gen_ts_client      (also `make ts-client`; needs Node 20+ and network for `npx`)

1. writes apps/api/openapi.json from the route/model definitions (no server needed)
2. runs openapi-typescript -> apps/web/src/lib/api/schema.d.ts   (types for every path, request and response)
`python -m scripts.export_openapi --check` fails CI when the committed openapi.json is stale.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from scripts.export_openapi import OUT, render

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "apps" / "web" / "src" / "lib" / "api" / "schema.d.ts"


def main() -> int:
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    npx = shutil.which("npx")
    if npx is None:
        print("npx not found: install Node 20+ (https://nodejs.org) and retry", file=sys.stderr)
        return 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    cmd = [npx, "--yes", "openapi-typescript@7", str(OUT), "-o", str(DEST)]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
