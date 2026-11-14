"""Typed hand-offs between agents (assignment C3).

Anything a later node *decides on* is one of these Pydantic objects, never free text.
Starter-kit field names are kept so existing tests still pass.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskFlag(StrEnum):
    UNPAID_WORK = "unpaid_work"
    OFF_PLATFORM_CONTACT = "off_platform_contact"
    OFF_PLATFORM_PAYMENT = "off_platform_payment"
    PROMPT_INJECTION = "prompt_injection"
    SCAM_PATTERN = "scam_pattern"
    LOW_BUDGET = "low_budget"
    UNVERIFIED_PAYMENT = "unverified_payment"
    POOR_CLIENT_HISTORY = "poor_client_history"
    DUPLICATE = "duplicate"
    GUARANTEE_REQUEST = "guarantee_request"


class Requirement(Strict):
    id: str
    text: str
    kind: Literal["skill", "deliverable", "constraint", "hidden"] = "skill"
    must_have: bool = True


class JobSpec(Strict):
    job_id: str
    title: str
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list)
    screening_questions: list[str] = Field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str = "USD"
    duration_days: int | None = None
    connects_cost: int | None = None
    category: str | None = None


class RiskReport(Strict):
    score: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list)
    explanation: str = ""
    client_score: float | None = Field(default=None, ge=0, le=1)
    injection_detected: bool = False


class RequirementMatch(Strict):
    requirement_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    strength: float = Field(default=0.0, ge=0, le=1)


class FitReport(Strict):
    score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    matches: list[RequirementMatch] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class BidDecision(Strict):
    action: Literal["skip", "watch", "bid"]
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class PriceQuote(Strict):
    amount: float = Field(ge=0)
    currency: str = "USD"
    pricing_type: Literal["fixed", "hourly"] = "fixed"
    band_low: float | None = None
    band_high: float | None = None
    floor: float | None = None
    connects_cost: int = 0
    connects_worth_it: bool = True
    rationale: str = ""


class Claim(Strict):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    label: Literal["supported", "partly_supported", "unsupported"] | None = None


class ProposalDraft(Strict):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    status: str = "pending_review"
    revision: int = 0
    strategy: str | None = None  # X2 A/B experiment arm


class Critique(Strict):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    grounding: float = Field(default=0.0, ge=0, le=1)
    personalisation: float = Field(default=0.0, ge=0, le=1)
    length_ok: bool = True


class PolicyViolation(Strict):
    rule_id: str
    message: str
    severity: Literal["block", "redact", "warn"] = "block"


class ComplianceVerdict(Strict):
    allowed: bool
    violations: list[PolicyViolation] = Field(default_factory=list)
    cleaned_cover_letter: str | None = None
    cleaned_screening_answers: list[str] | None = None
    policy_version: str = ""
