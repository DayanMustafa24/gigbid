"""The signed-in user's OWN portfolio, stored in the SCANNED layout that `rag.ingest.loaders` already understands:

    data/user/personas/user-<id>/profile.json   case_studies/<slug>.md   repos/<slug>/README.md   cv.md

Only text uploads (Markdown / plain text). Names are reduced to a safe slug and the path is built here from a fixed table, so a client can
never choose a path (no traversal). Per-user caps bound disk use. The persona is private to its owner (see agent.runtime.persona_visible).
Anything written invalidates the cached corpus so the next run re-indexes (the embedding cache is content-addressed: only new text is embedded).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from agent.runtime import ROOT, USER_PERSONAS, user_persona_id

MAX_FILES = 200
MAX_TOTAL_CHARS = 2_000_000
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-")[:60] or "item"


def base_dir(owner: str) -> Path:
    return ROOT / USER_PERSONAS / user_persona_id(owner)


def _target(base: Path, kind: str, name: str) -> Path:
    if kind == "case_study":
        return base / "case_studies" / f"{slugify(name)}.md"
    if kind == "readme":
        return base / "repos" / slugify(name) / "README.md"
    if kind == "cv":
        return base / "cv.md"
    raise ValueError(f"unknown kind {kind!r}")


def list_files(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind, pattern in (("case_study", "case_studies/*.md"), ("readme", "repos/*/README.md"), ("cv", "cv.md")):
        for p in sorted(base.glob(pattern)):
            name = p.parent.name if kind == "readme" else p.stem
            out.append({"kind": kind, "name": name, "chars": len(p.read_text(encoding="utf-8", errors="replace"))})
    return out


def read_profile(base: Path) -> dict[str, Any]:
    f = base / "profile.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def write_profile(base: Path, profile: dict[str, Any]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in profile.items() if v not in (None, "", [])}
    clean["skills"] = list(dict.fromkeys(profile.get("skills") or []))
    (base / "profile.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")


def save_file(base: Path, kind: str, name: str, text: str) -> dict[str, Any]:
    files = list_files(base)
    target = _target(base, kind, name)
    replacing = target.exists()
    if not replacing and len(files) >= MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files per portfolio")
    other = sum(f["chars"] for f in files) - (len(target.read_text(encoding="utf-8", errors="replace")) if replacing else 0)
    if other + len(text) > MAX_TOTAL_CHARS:
        raise ValueError("portfolio size limit reached")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"kind": kind, "name": target.parent.name if kind == "readme" else target.stem, "chars": len(text), "replaced": replacing}


def delete_file(base: Path, kind: str, name: str) -> bool:
    target = _target(base, kind, name)
    if not target.exists():
        return False
    target.unlink()
    if kind == "readme":
        shutil.rmtree(target.parent, ignore_errors=True)
    return True


def is_ready(base: Path) -> bool:
    return (base / "profile.json").exists() and any(f["kind"] in ("case_study", "readme") for f in list_files(base))
