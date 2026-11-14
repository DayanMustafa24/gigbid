"""Generate the three synthetic persona corpora.

Run: uv run python -m scripts.synth.build_personas [--seed 20261114] [--out data/synthetic/personas]
Deterministic: same seed -> byte-identical output (PDF uses reportlab invariant mode).
Output per persona: profile.json, cv.md, cv.pdf, case_studies/*.md, repos/<slug>/{README.md,src/*},
reviews.jsonl, proposals.jsonl, evidence.jsonl (gold catalogue), manifest.json. All records carry synthetic=true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

from . import render
from .personas import PERSONAS, Persona, Project

ROOT = Path(__file__).resolve().parent.parent.parent
N_PROPOSALS = 150


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _jsonl(path: Path, rows: list[dict]) -> None:
    _w(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))


def named_skills(p: Project, text: str) -> list[str]:
    low = text.replace(p.slug, "").lower()  # codename in headings must not count as naming a skill
    return [s for s in p.true_skills if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", low)]


def cv_pdf(persona: Persona, md: str, path: Path) -> bool:
    try:
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return False
    styles = getSampleStyleSheet()
    flow = []
    for line in md.splitlines():
        if not line.strip():
            flow.append(Spacer(1, 6))
        elif line.startswith("# "):
            flow.append(Paragraph(line[2:], styles["Title"]))
        elif line.startswith("## "):
            flow.append(Paragraph(line[3:], styles["Heading2"]))
        else:
            flow.append(Paragraph(line.lstrip("- ").replace("**", "").replace("&", "&amp;"), styles["BodyText"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), invariant=1, title=persona.name).build(flow)
    return True


def build(persona: Persona, seed: int, out: Path) -> dict:
    rng = random.Random(f"{seed}:{persona.id}")
    base = out / persona.id
    evidence: list[dict] = []
    reviews: list[dict] = []
    dates: dict[str, str] = {}

    def add(p: Project, kind: str, text: str, rel: str, date: str, **extra) -> None:
        evidence.append({
            "evidence_id": f"{persona.id}:{p.slug}:{kind}", "persona": persona.id, "type": kind, "project": p.slug,
            "named_skills": named_skills(p, text), "gold_skills": list(p.true_skills), "date": date,
            "client_industry": p.industry, "path": rel, "sha1": hashlib.sha1(text.encode()).hexdigest()[:12],
            "synthetic": True, **extra})

    for p in persona.projects:
        date = render.project_date(rng, p)
        dates[p.slug] = date
        cs = render.case_study_md(persona, p, rng, date)
        _w(base / "case_studies" / f"{p.slug}.md", cs)
        add(p, "case_study", cs, f"case_studies/{p.slug}.md", date)
        if p.repo:
            rd = render.readme_md(persona, p, rng)
            _w(base / "repos" / p.slug / "README.md", rd)
            add(p, "readme", rd, f"repos/{p.slug}/README.md", date)
            for rel, code in render.code_files(persona.id, p).items():
                _w(base / "repos" / p.slug / rel, code)
                add(p, "code", code, f"repos/{p.slug}/{rel}", date)
        bullet = render.cv_bullet(p)
        add(p, "cv_bullet", bullet, "cv.md", date)
        if rng.random() < 0.85:
            rv = render.review(persona, p, rng, date)
            reviews.append({**rv, "persona": persona.id, "synthetic": True})
            add(p, "review", rv["text"], "reviews.jsonl", date, rating=rv["rating"])

    cv = render.cv_md(persona, list(persona.projects))
    _w(base / "cv.md", cv)
    pdf_ok = cv_pdf(persona, cv, base / "cv.pdf")

    proposals = []
    for i in range(N_PROPOSALS):
        d = f"{rng.choice((2025, 2026))}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        pr = render.proposal(persona, rng, i, d)
        proposals.append(pr)
        lead = next(c["supported_by"] for c in pr["claims"] if c["supported_by"]) if any(c["supported_by"] for c in pr["claims"]) else None
        evidence.append({
            "evidence_id": pr["proposal_id"], "persona": persona.id, "type": "proposal", "project": lead,
            "named_skills": [], "gold_skills": [], "date": d, "client_industry": pr["job"]["industry"],
            "path": "proposals.jsonl", "sha1": hashlib.sha1(pr["text"].encode()).hexdigest()[:12],
            "outcome": pr["outcome"], "synthetic": True})
    proposals.sort(key=lambda x: x["date"])
    _jsonl(base / "proposals.jsonl", proposals)
    _jsonl(base / "reviews.jsonl", reviews)
    _jsonl(base / "evidence.jsonl", evidence)
    _w(base / "profile.json", json.dumps({
        "freelancer_id": persona.id, "display_name": persona.name, "headline": persona.headline,
        "hourly_rate_usd": persona.hourly_rate, "minimum_project_budget_usd": persona.floor_fixed,
        "years_experience": persona.years, "skills": persona.all_skills(), "synthetic": True}, indent=2))
    manifest = {
        "persona": persona.id, "seed": seed, "synthetic": True, "case_studies": len(persona.projects),
        "repos": sum(p.repo for p in persona.projects), "reviews": len(reviews), "proposals": len(proposals),
        "evidence_items": len(evidence), "cv_pdf": pdf_ok,
        "outcomes": dict(Counter(x["outcome"] for x in proposals)),
        "fabricated_claim_proposals": sum(x["features"]["contains_fabricated_claim"] for x in proposals),
    }
    _w(base / "manifest.json", json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic" / "personas")
    ap.add_argument("--personas", nargs="*", default=list(PERSONAS))
    a = ap.parse_args()
    for pid in a.personas:
        print(json.dumps(build(PERSONAS[pid], a.seed, a.out)))


if __name__ == "__main__":
    main()
