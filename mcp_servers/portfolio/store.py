"""Portfolio read model: pure Python, no MCP imports (unit-testable, reused by the RAG ingest later).

Layout read (produced by scripts/synth/build_personas.py, or a real portfolio exported in the same shape):
  <root>/<freelancer_id>/{profile.json, evidence.jsonl, reviews.jsonl, proposals.jsonl, case_studies/*.md, repos/**}

Leakage rule: evidence.jsonl carries `gold_skills` (the eval answer key). It is NEVER returned by any method here.
Only text-visible fields are exposed, so agents (and evals) cannot read the labels through the tool layer.

search() is a deliberately simple lexical BM25 baseline with filters. Phase 4 swaps in the hybrid/reranked retriever
behind the same MCP tool, so the tool contract does not change.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "data" / "synthetic" / "personas"

EvidenceType = Literal["case_study", "readme", "code", "review", "cv", "proposal"]

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
_STOP = frozenset(
    "a an and are as at be by for from has have i in is it of on or that the this to was we with you your our "
    "will can need needs looking want who work project help".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in (x.rstrip(".-") for x in _TOKEN.findall(text.lower())) if t and t not in _STOP]


# ---- public models ---------------------------------------------------------------------------------------------


class EvidenceFilters(BaseModel):
    types: list[EvidenceType] | None = Field(default=None, description="Keep only these evidence types.")
    project: str | None = Field(default=None, description="Keep only one project slug, e.g. 'support-rag-bot'.")
    industry: str | None = Field(default=None, description="Client industry, case-insensitive, e.g. 'SaaS'.")
    since: str | None = Field(default=None, description="ISO date; keep evidence dated on/after it.")
    skills: list[str] | None = Field(default=None, description="Keep items whose text mentions ALL these skills.")


class EvidenceHit(BaseModel):
    evidence_id: str
    project: str
    type: str
    title: str
    date: str | None = None
    client_industry: str | None = None
    score: float
    snippet: str


class SearchResult(BaseModel):
    freelancer_id: str
    requirement: str
    results: list[EvidenceHit]
    candidates: int = Field(description="How many evidence items passed the filters before ranking.")


class Review(BaseModel):
    rating: float
    text: str
    date: str | None = None
    client_industry: str | None = None


class CaseStudy(BaseModel):
    freelancer_id: str
    project: str
    title: str
    markdown: str
    metadata: dict[str, str]
    evidence_ids: list[str]
    reviews: list[Review]


class SkillStat(BaseModel):
    skill: str
    evidence_items: int
    projects: list[str]


class SkillList(BaseModel):
    freelancer_id: str
    declared_skills: list[str]
    evidenced: list[SkillStat] = Field(description="Skills named in the evidence text, with where they appear.")
    declared_without_evidence: list[str] = Field(
        description="Declared on the profile but not named in any evidence text. Do not claim these as proven."
    )


class Profile(BaseModel):
    freelancer_id: str
    display_name: str
    headline: str
    hourly_rate_usd: float
    minimum_project_budget_usd: float
    years_experience: float | None = None
    synthetic: bool = False


class PastProposal(BaseModel):
    proposal_id: str
    freelancer_id: str
    date: str | None = None
    job_title: str | None = None
    bid_price: float | None = None
    outcome: str | None = None
    text: str


class PortfolioError(ValueError):
    """Raised for caller mistakes (unknown id, ambiguous persona). Surfaced to the agent as a tool error."""


# ---- internals --------------------------------------------------------------------------------------------------


class _Doc:
    __slots__ = ("meta", "body", "tokens", "tf", "title")

    def __init__(self, meta: dict, body: str, title: str):
        self.meta, self.body, self.title = meta, body, title
        self.tokens = tokenize(body)
        self.tf = Counter(self.tokens)


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2].lstrip("\n")


def _title_of(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _best_snippet(body: str, q_tokens: set[str], width: int = 320) -> str:
    chunks = [c.strip() for c in re.split(r"\n\s*\n|\n(?=[-*#])", body) if c.strip()]
    if not chunks:
        return body[:width]
    best = max(chunks, key=lambda c: len(q_tokens & set(tokenize(c))))
    return best if len(best) <= width else best[: width - 1].rstrip() + "…"


class PersonaIndex:
    """One freelancer's evidence + BM25 index. Built lazily, once."""

    K1, B = 1.5, 0.75

    def __init__(self, base: Path):
        self.base = base
        self.freelancer_id = base.name
        self.docs: list[_Doc] = []
        for row in _read_jsonl(base / "evidence.jsonl"):
            p = base / row["path"]
            if not p.exists():
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            meta_fm, body = _split_front_matter(raw)
            meta = {k: v for k, v in row.items() if k != "gold_skills"}  # answer key never leaves this module
            meta["front_matter"] = meta_fm
            self.docs.append(_Doc(meta, body, _title_of(body, row.get("project") or row["evidence_id"])))
        n = len(self.docs)
        self.avgdl = (sum(len(d.tokens) for d in self.docs) / n) if n else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.tf.keys())
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.by_id = {d.meta["evidence_id"]: d for d in self.docs}

    def _score(self, doc: _Doc, q: list[str]) -> float:
        s, dl = 0.0, len(doc.tokens) or 1
        for t in set(q):
            f = doc.tf.get(t, 0)
            if f:
                s += self.idf.get(t, 0.0) * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * dl / (self.avgdl or 1)))
        return s

    @staticmethod
    def _passes(doc: _Doc, f: EvidenceFilters | None) -> bool:
        if f is None:
            return True
        m = doc.meta
        if f.types and m.get("type") not in f.types:
            return False
        if f.project and m.get("project") != f.project:
            return False
        if f.industry and (m.get("client_industry") or "").lower() != f.industry.lower():
            return False
        if f.since and (m.get("date") or "") < f.since:
            return False
        if f.skills:
            low = doc.body.lower()
            if not all(sk.lower() in low for sk in f.skills):
                return False
        return True

    def search(self, requirement: str, k: int, filters: EvidenceFilters | None) -> SearchResult:
        q = tokenize(requirement)
        cands = [d for d in self.docs if self._passes(d, filters)]
        scored = sorted(((self._score(d, q), d) for d in cands), key=lambda x: (-x[0], x[1].meta["evidence_id"]))
        qset = set(q)
        hits = [
            EvidenceHit(
                evidence_id=d.meta["evidence_id"],
                project=d.meta.get("project") or "",
                type=d.meta.get("type") or "",
                title=d.title,
                date=d.meta.get("date"),
                client_industry=d.meta.get("client_industry"),
                score=round(s, 4),
                snippet=_best_snippet(d.body, qset),
            )
            for s, d in scored[:k]
            if s > 0
        ]
        return SearchResult(freelancer_id=self.freelancer_id, requirement=requirement, results=hits, candidates=len(cands))


class PortfolioStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("PORTFOLIO_DATA_DIR") or DEFAULT_DIR)
        self._idx: dict[str, PersonaIndex] = {}

    # -- persona resolution (tenant scoping: every call is bound to exactly one freelancer) --
    def freelancers(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / "evidence.jsonl").exists())

    def resolve(self, freelancer_id: str | None) -> str:
        fid = freelancer_id or os.environ.get("PORTFOLIO_DEFAULT_FREELANCER")
        avail = self.freelancers()
        if not fid and len(avail) == 1:
            fid = avail[0]
        if not fid:
            raise PortfolioError(f"freelancer_id is required; available: {avail}")
        if fid not in avail:
            raise PortfolioError(f"unknown freelancer_id {fid!r}; available: {avail}")
        return fid

    def _index(self, fid: str) -> PersonaIndex:
        if fid not in self._idx:
            self._idx[fid] = PersonaIndex(self.root / fid)
        return self._idx[fid]

    # -- tools --
    def search_evidence(
        self, requirement: str, k: int = 5, filters: EvidenceFilters | None = None, freelancer_id: str | None = None
    ) -> SearchResult:
        if not requirement.strip():
            raise PortfolioError("requirement must not be empty")
        fid = self.resolve(freelancer_id)
        return self._index(fid).search(requirement, max(1, min(k, 25)), filters)

    def get_case_study(self, case_id: str, freelancer_id: str | None = None) -> CaseStudy:
        fid = self.resolve(freelancer_id)
        idx = self._index(fid)
        project = case_id.split(":")[1] if case_id.count(":") >= 2 else case_id
        doc = idx.by_id.get(f"{fid}:{project}:case_study")
        if doc is None:
            known = sorted({d.meta["project"] for d in idx.docs if d.meta.get("type") == "case_study"})
            raise PortfolioError(f"unknown case study {case_id!r} for {fid}; known projects: {known}")
        fm = doc.meta.get("front_matter") or {}
        reviews = [
            Review(rating=r["rating"], text=r["text"], date=r.get("date"), client_industry=r.get("client_industry"))
            for r in _read_jsonl(idx.base / "reviews.jsonl")
            if r.get("project") == project
        ]
        return CaseStudy(
            freelancer_id=fid,
            project=project,
            title=doc.title,
            markdown=(idx.base / doc.meta["path"]).read_text(encoding="utf-8", errors="replace"),
            metadata=fm,
            evidence_ids=sorted(d.meta["evidence_id"] for d in idx.docs if d.meta.get("project") == project),
            reviews=reviews,
        )

    def list_skills(self, freelancer_id: str | None = None) -> SkillList:
        fid = self.resolve(freelancer_id)
        idx = self._index(fid)
        profile = self.get_profile_raw(fid)
        declared: list[str] = profile.get("skills", [])
        stats: list[SkillStat] = []
        missing: list[str] = []
        for sk in declared:
            pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(sk)}(?![A-Za-z0-9])", re.IGNORECASE)
            hits = [d for d in idx.docs if pat.search(d.body)]
            if hits:
                stats.append(SkillStat(skill=sk, evidence_items=len(hits), projects=sorted({d.meta["project"] for d in hits if d.meta.get("project")})))
            else:
                missing.append(sk)
        stats.sort(key=lambda s: (-s.evidence_items, s.skill.lower()))
        return SkillList(freelancer_id=fid, declared_skills=declared, evidenced=stats, declared_without_evidence=missing)

    def get_profile_raw(self, fid: str) -> dict:
        p = self.root / fid / "profile.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def get_profile(self, freelancer_id: str | None = None) -> Profile:
        fid = self.resolve(freelancer_id)
        raw = self.get_profile_raw(fid)
        return Profile(
            freelancer_id=fid,
            display_name=raw.get("display_name", fid),
            headline=raw.get("headline", ""),
            hourly_rate_usd=raw.get("hourly_rate_usd", 0),
            minimum_project_budget_usd=raw.get("minimum_project_budget_usd", 0),
            years_experience=raw.get("years_experience"),
            synthetic=bool(raw.get("synthetic", False)),
        )

    # -- resources --
    def get_past_proposal(self, proposal_id: str) -> PastProposal:
        fid = proposal_id.split("-prop-")[0]
        fid = self.resolve(fid if "-prop-" in proposal_id else None)
        for r in _read_jsonl(self.root / fid / "proposals.jsonl"):
            if r.get("proposal_id") == proposal_id:
                job = r.get("job") or {}
                return PastProposal(  # `claims` / `features` hold eval labels (fabrication flags): not exposed
                    proposal_id=proposal_id,
                    freelancer_id=fid,
                    date=r.get("date"),
                    job_title=job.get("title"),
                    bid_price=r.get("bid_price"),
                    outcome=r.get("outcome"),
                    text=r.get("text", ""),
                )
        raise PortfolioError(f"unknown proposal {proposal_id!r}")
