"""Claim verification (assignment B3): split a draft into claims, label each against retrieved evidence, then remove or
rewrite what the evidence does not back BEFORE the Critic sees the draft.

Deterministic core (no LLM, runs in CI): numbers must appear in the evidence, content words must be covered, and a few
high-risk assertions (PhD, Fortune 500, team size, years of experience, user counts) need explicit support. An optional
LLM judge (prompt agent/prompts/rag_verify.md) only re-labels PARTLY supported claims; it can never rescue an
unsupported one. Rates are reported before and after repair.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.handoffs import Claim

from .llm import LLM, load_prompt

_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#\-]*")
_YEARS = re.compile(r"(\d+)\+?\s+years?", re.I)
_CUE = re.compile(
    r"\b(i (?:built|led|used|delivered|hold|have|worked|managed|created|developed|reduced|increased|improved|"
    r"shipped|designed|trained|deployed)|on the project|phd|degree|years|clients?|team of|worked (?:at|for|with)|"
    r"my team|we (?:built|shipped|delivered))\b", re.I)
_HARD = ("phd", "fortune", "million", "billion", "led a team", "managed a team", "award")
_STOP = frozenset(
    "a an and are as at be by for from has have i in is it of on or that the this to was we with you your our will can "
    "my me am been into than then them they their there these those who whom what when where which while about over "
    "project projects work worked built used use using client clients help helped experience similar many very "
    "also just more most some such per via one two three".split())


def split_claims(draft: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(" ".join(draft.split())) if s.strip()]


def kind_of(sentence: str) -> str:
    """factual | question | other (puffery, offers, closers: not verifiable, never counted as claims)."""
    if sentence.endswith("?"):
        return "question"
    if _NUM.search(sentence) or _CUE.search(sentence):
        return "factual"
    return "other"


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _content(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


_PCT = re.compile(r"(\d)\s*(?:percent|per cent|pct)\b", re.I)


def _nums(text: str) -> set[str]:
    text = _PCT.sub(r"\1%", text.replace(",", ""))  # '38 percent' == '38%'
    return {n.rstrip(".,") for n in _NUM.findall(text)}


def _ev_text(e: dict) -> str:
    return e.get("text") or e.get("snippet") or ""


@dataclass
class VerificationReport:
    claims: list[Claim]
    clean_text: str
    removed: list[str] = field(default_factory=list)
    rewritten: list[dict] = field(default_factory=list)
    factual_before: int = 0
    unsupported_before: int = 0
    partly_before: int = 0
    factual_after: int = 0
    unsupported_after: int = 0

    @property
    def rate_before(self) -> float:
        return self.unsupported_before / self.factual_before if self.factual_before else 0.0

    @property
    def rate_after(self) -> float:
        return self.unsupported_after / self.factual_after if self.factual_after else 0.0


def _check(sentence: str, evidence: list[dict], facts: dict[str, Any] | None) -> dict:
    texts = [_ev_text(e) for e in evidence]
    blob = " ".join(texts).lower()
    ev_words = set().union(*[_content(t) for t in texts]) if texts else set()
    ev_nums = _nums(blob)
    words = _content(sentence)
    cov = len(words & ev_words) / len(words) if words else 1.0
    nums = _nums(sentence)
    years_ok: set[str] = set()
    ym = _YEARS.search(sentence)
    if ym and facts and facts.get("years_experience") is not None and int(ym.group(1)) <= float(facts["years_experience"]):
        years_ok.add(ym.group(1))
    missing = sorted(n for n in nums if n not in ev_nums and n not in years_ok)
    hard = [h for h in _HARD if h in sentence.lower() and h not in blob]
    if hard or (nums and len(missing) == len(nums)):
        label, why = "unsupported", f"not in evidence: {', '.join(hard or missing)}"
    elif missing:
        label, why = "partly_supported", f"number(s) not in evidence: {', '.join(missing)}"
    elif cov >= 0.8:
        label, why = "supported", "numbers and key terms found in evidence"
    elif cov >= 0.5:
        label, why = "partly_supported", f"only {cov:.0%} of key terms found in evidence"
    else:
        label, why = "unsupported", f"only {cov:.0%} of key terms found in evidence"
    scored = sorted(((len(words & _content(_ev_text(e))), e.get("evidence_id", "")) for e in evidence), reverse=True)
    ids = [i for n, i in scored[:2] if n > 0 and i] if label != "unsupported" else []
    return {"label": label, "coverage": round(cov, 3), "missing_numbers": missing, "reason": why, "evidence_ids": ids}


def _judge(llm: LLM | None, sentence: str, evidence: list[dict]) -> str | None:
    if llm is None:
        return None
    try:
        sys_p, _ = load_prompt("rag_verify")
        ev = "\n---\n".join(_ev_text(e) for e in evidence)[:2000]
        ans = llm.complete(sys_p, f"<claim>{sentence}</claim>\n<evidence>{ev}</evidence>", max_tokens=8).strip().lower()
    except Exception:
        return None
    return next((x for x in ("partly_supported", "unsupported", "supported") if ans.replace(" ", "_").startswith(x)), None)


def verify_claims(draft: str, evidence: list[dict], facts: dict[str, Any] | None = None,
                  llm: LLM | None = None) -> list[dict]:
    """Starter-kit entry point. One dict per sentence: text, kind, label (None when not a factual claim), ..."""
    out = []
    for s in split_claims(draft):
        k = kind_of(s)
        if k != "factual":
            out.append({"text": s, "kind": k, "label": None, "coverage": None, "missing_numbers": [],
                        "reason": "not a verifiable factual claim", "evidence_ids": []})
            continue
        r = _check(s, evidence, facts)
        if r["label"] == "partly_supported":  # the judge may only move a partly-supported claim, never rescue unsupported
            j = _judge(llm, s, evidence)
            if j:
                r["label"], r["reason"] = j, r["reason"] + f" (judge: {j})"
        out.append({"text": s, "kind": k, **r})
    return out


def _trim_unsupported_clause(sentence: str, missing: list[str]) -> str | None:
    """Drop the clause that carries an unbacked number; keep the rest if it is still a sentence."""
    parts = re.split(r"(,\s+|\s+and\s+|;\s+|\s+with\s+|\s+which\s+)", sentence)
    keep = [p for p in parts if not any(m in p.replace(",", "") for m in missing)]
    text = "".join(keep).strip(" ,;")
    text = re.sub(r"(,|;|\band\b|\bwith\b|\bwhich\b)\s*$", "", text).strip(" ,;")
    if len(text.split()) < 4 or text == sentence:
        return None
    return text if text.endswith((".", "!", "?")) else text + "."


def verify_and_repair(draft: str, evidence: list[dict], facts: dict[str, Any] | None = None,
                      llm: LLM | None = None) -> VerificationReport:
    rows = verify_claims(draft, evidence, facts, llm)
    fact = [r for r in rows if r["kind"] == "factual"]
    rep = VerificationReport(
        claims=[Claim(text=r["text"], evidence_ids=r["evidence_ids"], label=r["label"]) for r in fact],
        clean_text="", factual_before=len(fact),
        unsupported_before=sum(r["label"] == "unsupported" for r in fact),
        partly_before=sum(r["label"] == "partly_supported" for r in fact))
    kept: list[str] = []
    for r in rows:
        if r["kind"] != "factual" or r["label"] == "supported":
            kept.append(r["text"])
        elif r["label"] == "unsupported":
            rep.removed.append(r["text"])
        else:  # partly supported: rewrite to the backed part, else keep only if the judge/coverage said it is mostly backed
            new = _trim_unsupported_clause(r["text"], r["missing_numbers"]) if r["missing_numbers"] else None
            if new and _check(new, evidence, facts)["label"] != "unsupported":
                kept.append(new)
                rep.rewritten.append({"from": r["text"], "to": new})
            elif r["missing_numbers"]:
                rep.removed.append(r["text"])
            else:
                kept.append(r["text"])
    rep.clean_text = " ".join(kept)
    after = [x for x in verify_claims(rep.clean_text, evidence, facts) if x["kind"] == "factual"]
    rep.factual_after = len(after)
    rep.unsupported_after = sum(x["label"] == "unsupported" for x in after)
    return rep
