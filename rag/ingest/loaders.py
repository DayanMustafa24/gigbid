"""Ingestion: turn a persona folder into a Corpus. No LLM, no network, no answer-key access.

Two layouts are understood:
  1. CATALOGUED  <persona>/evidence.jsonl exists (our synthetic personas; also a real portfolio exported the same way).
     Rows give evidence_id/type/project/date/client_industry/sha1. `gold_skills` is never read.
  2. SCANNED     no evidence.jsonl: the loader walks the folder (your own portfolio!):
       case_studies/*.md  repos/<slug>/README.md  repos/<slug>/**/<code>  cv.md|cv.pdf  reviews.jsonl  proposals.jsonl
Metadata on every item: persona, type, project, skills, date, client industry, proposal outcome (assignment B1).
Incremental re-indexing: `Corpus.diff(new)` lists added/changed/removed ids; the embedding cache is content-addressed,
so a re-index only embeds text it has not seen.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..corpus import Corpus, first_heading
from ..types import Item

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs", ".sql", ".r", ".yml", ".yaml", ".sh"}
_WORD = re.compile(r"[a-z0-9]+")


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2].lstrip("\n")


def read_cv_text(base: Path) -> str:
    md = base / "cv.md"
    if md.exists():
        return md.read_text(encoding="utf-8", errors="replace")
    pdf = base / "cv.pdf"
    if pdf.exists():
        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages)
    return ""


def cv_bullets(cv: str) -> list[str]:
    sec = re.split(r"^##\s+", cv, flags=re.M)
    chosen = next((s for s in sec if s.lower().startswith(("selected projects", "projects", "experience"))), cv)
    return [ln[2:].strip() for ln in chosen.splitlines() if ln.startswith("- ")]


def find_skills(text: str, vocab: list[str]) -> tuple[str, ...]:
    low = text.lower()
    found = [s for s in vocab if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", low)]
    return tuple(found)


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / (len(ta | tb) or 1)


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    w = _WORD.findall(text.lower())
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def _link_proposals(items: list[Item], pid: str) -> tuple[dict[str, list[str]], list[Item]]:
    """Which projects does each past proposal talk about? Text-only: shared 5-word shingles with the case study."""
    cs = {it.project: _shingles(it.text) for it in items if it.type == "case_study" and it.project}
    links: dict[str, list[str]] = {}
    out: list[Item] = []
    for it in items:
        if it.type != "proposal":
            out.append(it)
            continue
        sh = _shingles(it.text)
        hit = sorted(p for p, s in cs.items() if sh & s)
        links[it.evidence_id] = hit
        out.append(Item(**{**it.__dict__, "project": hit[0] if hit else None}))
    return links, out


def _catalogued(base: Path, rows: list[dict], vocab: list[str]) -> list[Item]:
    cv = cv_bullets(read_cv_text(base))
    cv_by_sha = {_sha12(b): b for b in cv}
    reviews = _read_jsonl(base / "reviews.jsonl")
    rv_by_sha = {_sha12(r["text"]): r for r in reviews}
    props = {r["proposal_id"]: r for r in _read_jsonl(base / "proposals.jsonl")}
    cs_text: dict[str, str] = {}
    titles: dict[str, str] = {}
    for r in rows:
        if r["type"] == "case_study" and (base / r["path"]).exists():
            _, body = split_front_matter((base / r["path"]).read_text(encoding="utf-8", errors="replace"))
            cs_text[r["project"]] = body
            titles[r["project"]] = first_heading(body) or r["project"]
    items: list[Item] = []
    for r in rows:  # gold_skills is deliberately never touched
        t, proj = r["type"], r.get("project")
        rating = None
        if t in ("case_study", "readme", "code"):
            p = base / r["path"]
            if not p.exists():
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            text = split_front_matter(raw)[1] if t == "case_study" else raw
        elif t == "cv_bullet":
            text = cv_by_sha.get(r.get("sha1", ""))
            if text is None and cv and proj in cs_text:
                best = max(cv, key=lambda b: _overlap(b, cs_text[proj]))
                top = max(cs_text, key=lambda s: _overlap(best, cs_text[s]))  # bullet must belong to THIS project
                text = best if top == proj and _overlap(best, cs_text[proj]) > 0.1 else None
            if text is None:
                continue
        elif t == "review":
            rv = rv_by_sha.get(r.get("sha1", "")) or next((x for x in reviews if x.get("project") == proj), None)
            if rv is None:
                continue
            text, rating = rv["text"], rv.get("rating")
        elif t == "proposal":
            pr = props.get(r["evidence_id"])
            if pr is None:
                continue
            text, proj = pr["text"], None  # `project` on proposal rows is generator-derived: recomputed from text below
        else:
            continue
        items.append(Item(
            evidence_id=r["evidence_id"], persona=r.get("persona", base.name), type=t, project=proj, text=text,
            date=r.get("date"), client_industry=r.get("client_industry"), skills=find_skills(text, vocab),
            outcome=r.get("outcome") if t == "proposal" else None, rating=rating if rating is not None else r.get("rating"),
            project_title=titles.get(proj) if proj else None, sha1=r.get("sha1")))
    return items


def _scan(base: Path, vocab: list[str], max_code_bytes: int) -> list[Item]:
    pid = base.name
    items: list[Item] = []
    titles: dict[str, str] = {}
    cs_text: dict[str, str] = {}
    for p in sorted((base / "case_studies").glob("*.md")) if (base / "case_studies").exists() else []:
        fm, body = split_front_matter(p.read_text(encoding="utf-8", errors="replace"))
        slug = fm.get("project") or p.stem
        titles[slug], cs_text[slug] = first_heading(body) or slug, body
        items.append(Item(f"{pid}:{slug}:case_study", pid, "case_study", slug, body, fm.get("date"),
                          fm.get("client_industry"), find_skills(body, vocab), project_title=titles[slug]))
    repos = base / "repos"
    for d in sorted(x for x in repos.iterdir() if x.is_dir()) if repos.exists() else []:
        slug, n = d.name, 0
        rd = d / "README.md"
        if rd.exists():
            t = rd.read_text(encoding="utf-8", errors="replace")
            items.append(Item(f"{pid}:{slug}:readme", pid, "readme", slug, t, None, None, find_skills(t, vocab),
                              project_title=titles.get(slug)))
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix.lower() in CODE_EXT and f.stat().st_size <= max_code_bytes:
                t = f.read_text(encoding="utf-8", errors="replace")
                n += 1
                eid = f"{pid}:{slug}:code" + ("" if n == 1 else f":{n}")
                items.append(Item(eid, pid, "code", slug, t, None, None, find_skills(t, vocab),
                                  project_title=titles.get(slug)))
    for i, b in enumerate(cv_bullets(read_cv_text(base))):
        proj = max(cs_text, key=lambda s: _overlap(b, cs_text[s]), default=None)
        proj = proj if proj and _overlap(b, cs_text[proj]) > 0.1 else None
        items.append(Item(f"{pid}:{proj or 'cv'}:cv_bullet" + ("" if i == 0 or proj else f":{i}"), pid, "cv_bullet",
                          proj, b, None, None, find_skills(b, vocab), project_title=titles.get(proj or "")))
    seen: dict[str, int] = {}
    for r in _read_jsonl(base / "reviews.jsonl"):
        proj = r.get("project")
        seen[proj] = seen.get(proj, 0) + 1
        items.append(Item(f"{pid}:{proj}:review" + ("" if seen[proj] == 1 else f":{seen[proj]}"), pid, "review", proj,
                          r["text"], r.get("date"), r.get("client_industry"), find_skills(r["text"], vocab),
                          rating=r.get("rating"), project_title=titles.get(proj or "")))
    for r in _read_jsonl(base / "proposals.jsonl"):
        items.append(Item(r["proposal_id"], pid, "proposal", None, r["text"], r.get("date"),
                          (r.get("job") or {}).get("industry"), find_skills(r["text"], vocab), outcome=r.get("outcome")))
    return items


def load_persona(base: Path | str, *, max_code_bytes: int = 20_000) -> Corpus:
    base = Path(base)
    pf = base / "profile.json"
    profile = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    vocab = list(profile.get("skills", []))
    rows = _read_jsonl(base / "evidence.jsonl")
    items = _catalogued(base, rows, vocab) if rows else _scan(base, vocab, max_code_bytes)
    links, items = _link_proposals(items, base.name)
    return Corpus(persona=base.name, items=items, profile=profile, proposal_links=links, source=str(base))
