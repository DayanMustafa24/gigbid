"""Prompt-injection detector (assignment R9, layers two and three; layer one = quarantine + policy regex rules).

Job posts and client messages are untrusted DATA. Defence in depth, none of it relies on an LLM obeying instructions:
  0. normalise: NFKC + strip invisible/bidi characters + map common Cyrillic look-alikes, so obfuscated text ("ig​nore") cannot slip
     past the regex rules that run on the normalised text;
  1. policy regex rules (agent/policy/rules.yaml, `prompt_injection` flag) - applied by the Risk Assessor / liaison;
  2. structural heuristics here (role/delimiter forgery, tool-name mentions, AI-directed imperatives, hidden markup, encoded blobs);
  3. optional classifier hook (`Deps.injection_classifier`, e.g. the open-weight Prompt Guard configured under security.injection_classifier);
  4. structure: the analyst is tool-less and reads quoted text only; every write needs human approval. So even a missed injection cannot act.
Detection never decides alone: it raises a flag, it never blocks a good job (an injected sentence is ignored, not a reason to skip).
Thresholds are data (config `security.injection`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONFUSABLES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "ј": "j",
                              "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "І": "I"})

_WORDS = re.compile(r"\w+")
DEFAULT = {"high": 0.6, "medium": 0.3}
TOOL_NAMES = ("submit_proposal", "send_message", "create_proposal_draft", "hold_slot", "draft_email", "release_hold")

_SIGNALS: list[tuple[str, float, re.Pattern[str]]] = [
    ("role_or_delimiter_forgery", 0.6, re.compile(
        r"(?im)^\s*(?:system|assistant)\s*:|<\|?(?:im_start|im_end|system|endoftext)\|?>|\[/?inst\]|^#{2,}\s*(?:system|instruction)")),
    ("tool_name_mention", 0.6, re.compile(r"\b(?:" + "|".join(TOOL_NAMES) + r")\b", re.I)),
    ("ai_directed_imperative", 0.5, re.compile(
        r"\bas an ai\b|\byou are (?:now )?(?:a|an|the) [a-z ]{0,30}(?:assistant|bot|model|agent)\b|"
        r"\b(?:reveal|show|print|repeat|output) (?:your|the) (?:system )?(?:prompt|instructions)\b|"
        r"\bdisregard (?:the|all|any|your)\b|\bnew instructions?\s*:|\bfrom now on,? (?:you|ignore)\b|"
        r"\bdo not (?:tell|mention|inform) (?:the )?(?:user|freelancer)\b", re.I)),
    ("hidden_markup", 0.3, re.compile(r"<!--.*?-->", re.S)),
    ("encoded_blob", 0.3, re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")),
]


@dataclass
class Detection:
    score: float = 0.0
    level: str = "none"  # none | medium | high
    reasons: list[str] = field(default_factory=list)
    normalized_text: str = ""
    removed_chars: int = 0

    def as_dict(self) -> dict:
        return {"score": round(self.score, 3), "level": self.level, "reasons": self.reasons, "removed_chars": self.removed_chars}


def _fix_mixed_script(text: str) -> str:
    """Map Cyrillic look-alikes to Latin ONLY inside words that mix both scripts (the homoglyph-attack shape). Genuinely Russian
    or Ukrainian words are left untouched, so non-English job posts are not damaged."""
    def fix(m: re.Match[str]) -> str:
        w = m.group(0)
        latin = any("a" <= ch.lower() <= "z" for ch in w)
        cyrillic = any("Ѐ" <= ch <= "ӿ" for ch in w)
        return w.translate(_CONFUSABLES) if latin and cyrillic else w

    return _WORDS.sub(fix, text)


def normalise(text: str) -> tuple[str, int]:
    """(clean text, number of characters removed). Visible content is preserved; only invisible/control characters are dropped."""
    t = unicodedata.normalize("NFKC", text or "")
    stripped = _CONTROL.sub("", _INVISIBLE.sub("", t))
    return _fix_mixed_script(stripped), len(t) - len(stripped)


def level_for(score: float, cfg: dict | None = None) -> str:
    c = {**DEFAULT, **(cfg or {})}
    return "high" if score >= c["high"] else "medium" if score >= c["medium"] else "none"


def detect(text: str, cfg: dict | None = None) -> Detection:
    """Layer-two heuristics on the normalised text. Pure and deterministic. Benign text scores 0."""
    clean, removed = normalise(text)
    score, reasons = 0.0, []
    if removed:
        score += 0.4
        reasons.append(f"{removed} invisible/control character(s) removed")
    for name, weight, pat in _SIGNALS:
        if pat.search(clean):
            score += weight
            reasons.append(name)
    score = min(1.0, score)
    return Detection(score=score, level=level_for(score, cfg), reasons=reasons, normalized_text=clean, removed_chars=removed)
