"""Deterministic policy engine over agent/policy/rules.yaml (used by Compliance Guard + Risk Assessor)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from agent.handoffs import ComplianceVerdict, PolicyViolation
from agent.settings import ROOT, load_config

REDACTION = "[removed]"


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    applies_to: tuple[str, ...]
    type: str
    action: str
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class Policy:
    version: str
    rules: tuple[Rule, ...]
    costs: dict
    limits: dict


@lru_cache
def load_policy(path: str | None = None) -> Policy:
    p = Path(path) if path else ROOT / load_config()["policy_file"]
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    rules = tuple(
        Rule(
            id=r["id"],
            description=r.get("description", ""),
            applies_to=tuple(r.get("applies_to", [])),
            type=r["type"],
            action=r["action"],
            patterns=tuple(re.compile(x, re.I) for x in r.get("patterns", [])),
        )
        for r in raw["rules"]
    )
    return Policy(raw["policy_version"], rules, raw.get("costs", {}), raw.get("limits", {}))


def scan(text: str, scope: str, policy: Policy | None = None) -> list[PolicyViolation]:
    """Return one violation per matching regex rule that applies to `scope`."""
    policy = policy or load_policy()
    out: list[PolicyViolation] = []
    for rule in policy.rules:
        if rule.type != "regex" or scope not in rule.applies_to:
            continue
        if any(p.search(text) for p in rule.patterns):
            out.append(PolicyViolation(rule_id=rule.id, message=rule.description, severity=rule.action))
    return out


def redact(text: str, policy: Policy | None = None) -> str:
    policy = policy or load_policy()
    for rule in policy.rules:
        if rule.type == "regex" and rule.action == "redact" and "draft" in rule.applies_to:
            for p in rule.patterns:
                text = p.sub(REDACTION, text)
    return text


def check_draft(cover_letter: str, answers: list[str] | None = None,
                policy: Policy | None = None) -> ComplianceVerdict:
    """Deterministic compliance pass for a proposal draft."""
    policy = policy or load_policy()
    answers = answers or []
    violations = scan("\n".join([cover_letter, *answers]), "draft", policy)
    words = len(cover_letter.split())
    lim = policy.limits
    if lim.get("max_cover_letter_words") and words > lim["max_cover_letter_words"]:
        violations.append(PolicyViolation(rule_id="L-max-words", message="Cover letter too long", severity="warn"))
    cleaned = redact(cover_letter, policy)
    cleaned_answers = [redact(a, policy) for a in answers]
    blocked = any(v.severity == "block" for v in violations)
    return ComplianceVerdict(
        allowed=not blocked,
        violations=violations,
        cleaned_cover_letter=cleaned,
        cleaned_screening_answers=cleaned_answers,
        policy_version=policy.version,
    )
