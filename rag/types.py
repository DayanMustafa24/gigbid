"""Shared RAG types. Plain dataclasses: cheap, hashable, no framework needed.

Leakage rule (same as the Portfolio MCP server): `gold_skills` in evidence.jsonl is the eval answer key and is NEVER
read by ingestion. Chunk `skills` are derived from text + the declared profile vocabulary only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRIMARY_TYPES = ("case_study", "readme", "code", "cv_bullet", "review")  # what the writer may cite
SIGNAL_TYPES = ("proposal",)  # past proposals: outcome signal + phrasing, never cited as proof of skill
TYPE_PRIORITY = {t: i for i, t in enumerate(("case_study", "code", "readme", "review", "cv_bullet", "proposal"))}


@dataclass(frozen=True)
class Item:
    """One evidence item = one evidence_id (a case study, a README, a code file, a CV bullet, a review, a proposal)."""

    evidence_id: str
    persona: str
    type: str
    project: str | None
    text: str
    date: str | None = None
    client_industry: str | None = None
    skills: tuple[str, ...] = ()
    outcome: str | None = None  # proposals only: won | replied | ignored
    rating: float | None = None
    project_title: str | None = None
    sha1: str | None = None


@dataclass(frozen=True)
class Unit:
    """One retrievable piece (a chunk, or a multi-vector view of a project). Maps back to evidence ids."""

    uid: str
    evidence_ids: tuple[str, ...]
    project: str | None
    type: str
    text: str  # what BM25 / reranker see
    dense_text: str = ""  # what the embedder sees (defaults to text)
    meta: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass
class JobQuery:
    """Everything a retriever may look at. Never contains gold labels."""

    job_id: str
    title: str
    description: str
    required_skills: list[str] = field(default_factory=list)
    screening_questions: list[str] = field(default_factory=list)

    @property
    def whole_post(self) -> str:
        return f"{self.title}\n{self.description}".strip()

    @classmethod
    def from_dev_row(cls, row: dict) -> JobQuery:
        return cls(
            job_id=row["job_id"],
            title=row.get("title", ""),
            description=row.get("description", ""),
            required_skills=list(row.get("required_skills", [])),
            screening_questions=list(row.get("screening_questions", [])),
        )


@dataclass
class Retrieval:
    evidence_ids: list[str]
    scores: list[float] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    abstained: bool = False  # corrective variants: "no usable evidence, do not claim"
    meta: dict[str, Any] = field(default_factory=dict)
