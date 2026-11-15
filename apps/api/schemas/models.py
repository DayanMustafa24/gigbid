"""Request/response models. Everything the web app sends or receives is typed here (OpenAPI -> TypeScript client, Phase 6.3)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL = r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$"
Skill = Annotated[str, Field(min_length=1, max_length=60)]


# ---- auth -----------------------------------------------------------------------------------------------------------------------------
class Credentials(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=10, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: str


# ---- runs ----------------------------------------------------------------------------------------------------------------------------
class PortfolioFileIn(BaseModel):
    """One piece of the user's own portfolio, as text (case study / repo README as Markdown, CV as Markdown)."""

    kind: Literal["case_study", "readme", "cv"]
    name: str = Field(min_length=1, max_length=80)  # becomes the project slug; ignored for the CV
    text: str = Field(min_length=20, max_length=200_000)


class PortfolioPdfIn(BaseModel):
    """A PDF (typically the CV), sent base64-encoded in JSON; its text is extracted server-side and stored like any other file."""

    kind: Literal["case_study", "readme", "cv"]
    name: str = Field(min_length=1, max_length=80)
    pdf_base64: str = Field(min_length=20, max_length=7_500_000)  # ~5 MB of PDF


class PortfolioProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    headline: str | None = Field(default=None, max_length=160)
    skills: list[Skill] = Field(default_factory=list, max_length=80)  # the vocabulary used to tag evidence; claims still need evidence
    years_experience: int | None = Field(default=None, ge=0, le=60)
    hourly_rate_usd: float | None = Field(default=None, gt=0, le=10_000)
    minimum_project_budget_usd: float | None = Field(default=None, ge=0, le=10_000_000)


class PortfolioOut(BaseModel):
    persona: str
    profile: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)
    ready: bool = False  # has a profile and at least one case study or README to cite


# ---- runs ----------------------------------------------------------------------------------------------------------------------------
class RegenerateIn(BaseModel):
    """Regenerate ONE paragraph of the pending proposal. The result is a candidate the human can load into the editor; nothing is saved."""

    paragraph: int = Field(ge=0, le=50)
    instruction: str | None = Field(default=None, max_length=300)


class RegenerateOut(BaseModel):
    paragraph: int
    paragraph_text: str
    cover_letter: str
    removed_claims: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class JobInline(BaseModel):
    """A job pasted by the user (not on the connected marketplace). The approved proposal is copied by hand, never submitted."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=20, max_length=8000)
    budget_max: float = Field(gt=0, le=10_000_000)  # pricing needs a budget; hourly: the top of the range
    budget_min: float = Field(default=0, ge=0)
    budget_type: Literal["fixed", "hourly"] = "fixed"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    required_skills: list[Skill] = Field(default_factory=list, max_length=30)
    screening_questions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=10)
    connects_cost: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def _range(self) -> JobInline:
        if self.budget_min > self.budget_max:
            raise ValueError("budget_min must not exceed budget_max")
        return self


class RunCreate(BaseModel):
    kind: Literal["bid", "conversation"] = "bid"
    persona: str = Field(min_length=1, max_length=60)
    job_id: str | None = Field(default=None, max_length=120)  # bid: a job on the connected marketplace ...
    job: JobInline | None = None  # ... or a pasted one
    proposal_id: str | None = Field(default=None, max_length=120)  # conversation
    client_message: str | None = Field(default=None, min_length=1, max_length=5000)

    @model_validator(mode="after")
    def _shape(self) -> RunCreate:
        if self.kind == "bid":
            if (self.job_id is None) == (self.job is None):
                raise ValueError("a bid run needs exactly one of job_id or job")
        elif not (self.proposal_id and self.client_message):
            raise ValueError("a conversation run needs proposal_id and client_message")
        return self


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    subject_id: str
    persona: str
    status: str  # running | awaiting_approval | completed | skipped | closed | needs_attention | failed
    decision: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    pending: dict[str, Any] | None = None  # the approval request shown to the human (present while awaiting_approval)
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:  # SQLite returns naive datetimes; they are UTC, so say so (the UI converts to local time)
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class DecisionIn(BaseModel):
    action: Literal["approve", "edit", "reject", "dismiss"]
    edited_text: str | None = Field(default=None, min_length=1, max_length=5000)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _edit_needs_text(self) -> DecisionIn:
        if self.action == "edit" and not (self.edited_text and self.edited_text.strip()):
            raise ValueError("edited_text is required when action is 'edit'")
        return self


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    ts: datetime
    node: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


# ---- settings / personas / memory ---------------------------------------------------------------------------------------------------
class SettingsIn(BaseModel):
    default_persona: str | None = Field(default=None, max_length=60)
    scout_queries: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=10)
    # optional preferences (stored only when provided, so older clients keep working)
    hourly_rate_usd: float | None = Field(default=None, gt=0, le=10_000)
    min_project_budget_usd: float | None = Field(default=None, ge=0, le=10_000_000)
    weekly_connects_budget: int | None = Field(default=None, ge=0, le=1000)


class PersonaOut(BaseModel):
    id: str
    display_name: str | None = None
    headline: str | None = None
    hourly_rate_usd: float | None = None
    minimum_project_budget_usd: float | None = None


class ForgetIn(BaseModel):
    confirm: Literal["forget everything"]  # a typed phrase: erasure is not one click away from a mis-tap
