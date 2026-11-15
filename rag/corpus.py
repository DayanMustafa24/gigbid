"""In-memory corpus for one freelancer: items -> retrievable units (several views), outcome signal, incremental diff.

Pure Python + numpy-free. Built by rag.ingest.loaders, consumed by rag.index and rag.variants.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from .types import PRIMARY_TYPES, TYPE_PRIORITY, Item, Unit

OUTCOME_WEIGHT = {"won": 1.0, "replied": 0.5, "ignored": 0.0}


def item_sha(it: Item) -> str:
    return it.sha1 or hashlib.sha1(it.text.encode("utf-8")).hexdigest()[:12]


def fixed_chunks(text: str, size: int = 500, overlap: int = 80) -> list[str]:
    """Fixed-size character windows snapped to word boundaries (the naive V0 chunker)."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + size)
        if j < len(text):
            sp = text.rfind(" ", i + size // 2, j)
            j = sp if sp > i else j
        out.append(text[i:j].strip())
        if j >= len(text):
            break
        i = max(j - overlap, i + 1)
    return [c for c in out if c]


def context_line(it: Item) -> str:
    """Deterministic context header (V6 default, no LLM): who/what/when/which client + skills visible in the text."""
    bits = [f"{it.type.replace('_', ' ')}"]
    if it.project_title or it.project:
        bits.append(f"for the project '{it.project_title or it.project}'")
    if it.client_industry:
        bits.append(f"({it.client_industry} client")
        bits[-1] += f", {it.date[:4]})" if it.date else ")"
    elif it.date:
        bits.append(f"({it.date[:4]})")
    ctx = " ".join(bits)
    if it.skills:
        ctx += "; skills: " + ", ".join(it.skills[:8])
    return ctx + "."


@dataclass
class Corpus:
    persona: str
    items: list[Item]
    profile: dict = field(default_factory=dict)
    proposal_links: dict[str, list[str]] = field(default_factory=dict)  # proposal id -> projects named in its text
    source: str = ""  # directory the corpus was loaded from

    def __post_init__(self) -> None:
        self.by_id = {it.evidence_id: it for it in self.items}
        self.by_project: dict[str, list[Item]] = defaultdict(list)
        for it in self.items:
            if it.project and it.type in PRIMARY_TYPES:
                self.by_project[it.project].append(it)
        for v in self.by_project.values():
            v.sort(key=lambda x: (TYPE_PRIORITY.get(x.type, 9), x.evidence_id))

    # ---- views ------------------------------------------------------------------------------------------------
    def primary(self) -> list[Item]:
        return [it for it in self.items if it.type in PRIMARY_TYPES]

    def proposals(self) -> list[Item]:
        return [it for it in self.items if it.type == "proposal"]

    def project_items(self, project: str) -> list[str]:
        return [it.evidence_id for it in self.by_project.get(project, [])]

    def units_fixed(self, size: int = 500, overlap: int = 80, context: Callable[[Item], str] | None = None) -> list[Unit]:
        """V0 view. With `context` (V6) the generated line is prepended to BOTH the BM25 and the embedding text."""
        units: list[Unit] = []
        for it in self.primary():
            ctx = (context(it) + "\n") if context else ""
            for i, ch in enumerate(fixed_chunks(it.text, size, overlap)):
                t = ctx + ch
                units.append(Unit(f"{it.evidence_id}#{i}", (it.evidence_id,), it.project, it.type, t, t,
                                  {"view": "contextual" if context else "chunk"}))
        return units

    def units_multivector(self) -> list[Unit]:
        """V5 view: per project, separate summary / readme / code vectors; each maps back to the whole project."""
        units: list[Unit] = []
        for proj, its in sorted(self.by_project.items()):
            groups = {
                "summary": [i for i in its if i.type in ("case_study", "cv_bullet", "review")],
                "readme": [i for i in its if i.type == "readme"],
                "code": [i for i in its if i.type == "code"],
            }
            for view, members in groups.items():
                if members:
                    txt = "\n".join(m.text for m in members)
                    units.append(Unit(f"{self.persona}:{proj}:{view}", tuple(m.evidence_id for m in members), proj,
                                      view, txt, txt, {"view": view}))
        return units

    # ---- outcome signal (V4) ---------------------------------------------------------------------------------
    def outcome_boost(self, prior_n: float = 3.0) -> dict[str, float]:
        """Per project: smoothed outcome score of past proposals that named it, minus the global mean. Range ~[-1, 1]."""
        props = self.proposals()
        if not props:
            return {}
        gm = sum(OUTCOME_WEIGHT.get(p.outcome or "", 0.0) for p in props) / len(props)
        tot: dict[str, float] = defaultdict(float)
        cnt: dict[str, int] = defaultdict(int)
        for p in props:
            for proj in self.proposal_links.get(p.evidence_id, []):
                tot[proj] += OUTCOME_WEIGHT.get(p.outcome or "", 0.0)
                cnt[proj] += 1
        return {pr: (tot[pr] + prior_n * gm) / (cnt[pr] + prior_n) - gm for pr in cnt}

    # ---- incremental re-indexing ------------------------------------------------------------------------------
    def diff(self, new: Corpus) -> dict[str, list[str]]:
        old = {i.evidence_id: item_sha(i) for i in self.items}
        cur = {i.evidence_id: item_sha(i) for i in new.items}
        return {
            "added": sorted(set(cur) - set(old)),
            "removed": sorted(set(old) - set(cur)),
            "changed": sorted(k for k in set(cur) & set(old) if cur[k] != old[k]),
        }


_TITLE = re.compile(r"^#\s+(.+)$", re.M)


def first_heading(text: str) -> str | None:
    m = _TITLE.search(text)
    return m.group(1).strip() if m else None
