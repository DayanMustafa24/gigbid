"""Explicit, conservative promotion rules for long-term memory (assignment R7). Pure functions: no I/O, fully unit-tested.

Principles (docs/ARCHITECTURE.md "three separations"): State is per job, the trace is audit, long-term memory is PROMOTED only.
 1. Raw tool output / model output / job text / client text is never written to memory. Style memory is learned ONLY from the
    freelancer's own edits to a draft (source = human edit) and is stored as a fixed-vocabulary rule, not as free text.
    The one free-text exception (a sentence the user repeatedly deletes) is sanitised and checked against the injection rules.
 2. A pattern is promoted only when it is repeated (>= `min_edits` distinct proposals) AND consistent (>= `min_consistency` of
    the last `window` proposals where it could have appeared). Unchanged approvals count AGAINST a pattern.
 3. Promotion is idempotent, bounded (`max_rules`) and reversible: the owner can delete any item; a deleted item is never
    re-promoted (tombstone); items that stop qualifying are retired.
 4. Outcome memory replaces an ASSUMED rate with a MEASURED one only after `min_samples` real outcomes, shrunk toward the
    assumption (Beta prior) so a handful of results cannot swing pricing.
All thresholds are data (agent/config.yaml `memory:`), so changing a rule needs no code change.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9']+")
_TAGISH = re.compile(r"[<>`{}\[\]]")

DEFAULT_STYLE = {"min_edits": 3, "min_consistency": 0.7, "window": 8, "shorter_ratio": 0.8, "longer_ratio": 1.25,
                 "max_rules": 8, "max_phrase_chars": 90, "min_sentence_chars": 25, "match_jaccard": 0.6}


def should_promote_style_edit(edit_count: int, consistent_pattern: bool) -> bool:
    """Starter-kit signature, kept for compatibility: >= 3 repeated edits AND a consistent pattern. The config-driven rule used by the
    system is `should_promote` below (it also measures consistency instead of taking it as a flag)."""
    return edit_count >= 3 and consistent_pattern


def should_promote(hits: int, testable: int, style_cfg: dict[str, Any] | None = None) -> bool:
    c = {**DEFAULT_STYLE, **(style_cfg or {})}
    return hits >= c["min_edits"] and testable > 0 and hits / testable >= c["min_consistency"]


def norm_sentence(s: str) -> str:
    return " ".join(_WORD.findall(s.lower()))


def split_sentences(text: str, min_chars: int = 25) -> list[str]:
    out = [norm_sentence(s) for s in _SENT.split(text or "")]
    return [s for s in out if len(s) >= min_chars]


def _jac(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / (len(ta | tb) or 1)


def extract_signals(original: str, edited: str, style_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the human changed, as testable booleans. `edited == original` (approved as is) yields all-False pattern signals."""
    c = {**DEFAULT_STYLE, **(style_cfg or {})}
    wo, we = len(original.split()), len(edited.split())
    so, se = split_sentences(original, c["min_sentence_chars"]), split_sentences(edited, c["min_sentence_chars"])
    dropped = [s for s in so if not any(_jac(s, t) >= c["match_jaccard"] for t in se)]
    return {"wo": wo, "we": we, "changed": original.strip() != edited.strip(),
            "shorter": wo > 0 and we <= wo * c["shorter_ratio"], "longer": wo > 0 and we >= wo * c["longer_ratio"],
            "excl_testable": "!" in original, "no_excl": "!" not in edited,
            "q_added": edited.count("?") > original.count("?"),
            "orig": so[:30], "dropped": dropped[:30]}


@dataclass(frozen=True)
class Candidate:
    kind: str
    key: str
    text: str
    hits: int
    testable: int

    @property
    def consistency(self) -> float:
        return round(self.hits / self.testable, 4) if self.testable else 0.0


def safe_phrase(phrase: str, max_chars: int, is_injection) -> str | None:
    """A deleted sentence may become a 'never write this' rule only if it is plain text: no markup, no injection patterns, short."""
    p = _TAGISH.sub("", phrase).strip()
    if not p or len(p) > max_chars or is_injection(p):
        return None
    return p


def style_candidates(events: list[dict[str, Any]], style_cfg: dict[str, Any] | None = None, is_injection=lambda s: False) -> list[Candidate]:
    """Rules that currently qualify. `events` = signals of recent proposals, NEWEST FIRST. Deterministic and order-stable."""
    c = {**DEFAULT_STYLE, **(style_cfg or {})}
    ev = events[: c["window"]]
    n = len(ev)
    out: list[Candidate] = []

    def consider(kind: str, key: str, hits: int, testable: int, text: str) -> None:
        if should_promote(hits, testable, c):
            out.append(Candidate(kind, key, text, hits, testable))

    short = [e for e in ev if e.get("shorter")]
    if short:
        target = max(40, int(round(median(e["we"] for e in short) / 10.0) * 10))
        consider("length", "shorter", len(short), n, f"Keep the cover letter short: about {target} words or fewer.")
    long_ = [e for e in ev if e.get("longer")]
    if long_:
        target = int(round(median(e["we"] for e in long_) / 10.0) * 10)
        consider("length", "longer", len(long_), n, f"Write a fuller cover letter: about {target} words.")
    t_excl = [e for e in ev if e.get("excl_testable")]
    consider("tone", "no_exclamations", sum(1 for e in t_excl if e.get("no_excl")), len(t_excl), "Do not use exclamation marks.")
    consider("structure", "adds_question", sum(1 for e in ev if e.get("q_added")), n, "End with one specific question to the client.")
    dropped = Counter(s for e in ev for s in set(e.get("dropped", [])))
    for phrase, hits in dropped.items():
        testable = sum(1 for e in ev if phrase in e.get("orig", []))
        sp = safe_phrase(phrase, c["max_phrase_chars"], is_injection)
        if sp:
            consider("avoid", f"drop:{phrase}", hits, testable, f'Do not write sentences like: "{sp}"')
    out.sort(key=lambda x: (-x.hits, -x.consistency, x.key))
    # a 'shorter' and a 'longer' rule can never both hold: keep the stronger one
    kinds = {x.key for x in out}
    if {"shorter", "longer"} <= kinds:
        loser = min((x for x in out if x.key in ("shorter", "longer")), key=lambda x: (x.hits, x.consistency))
        out = [x for x in out if x is not loser]
    return out[: c["max_rules"]]
