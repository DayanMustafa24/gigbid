"""Workspace read/write model: a LOCAL MOCK of calendar + mailbox (documented stand-in, see docs/TOOLS.md).

Pure Python, no MCP imports. Persists to one JSON file (atomic replace). Safe by construction: there is NO send-email
operation anywhere; drafts stay drafts, and calendar holds are always TENTATIVE. A real Google Calendar/Gmail adapter
can replace this class later behind the same method names.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agent.settings import ROOT, load_config


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RecurringBusy(BaseModel):
    title: str
    days: list[int]
    start: str
    end: str


class WorkspaceConfig(BaseModel):
    utc_offset_hours: float = 0
    work_start: str = "09:00"
    work_end: str = "17:00"
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    granularity_min: int = 30
    buffer_min: int = 15
    min_notice_hours: float = 2
    max_range_days: int = 14
    state_file: str = "data/workspace/state.json"
    recurring_busy: list[RecurringBusy] = Field(default_factory=list)

    @classmethod
    def from_config(cls) -> WorkspaceConfig:
        return cls(**load_config().get("workspace", {}))


class Slot(BaseModel):
    start: str
    end: str


class Hold(BaseModel):
    hold_id: str
    title: str
    start: str
    end: str
    status: str = "tentative"          # tentative | released   (never "confirmed": a human confirms in the real calendar)
    proposal_id: str | None = None


class EmailDraft(BaseModel):
    draft_id: str
    to: str
    subject: str
    body: str
    proposal_id: str | None = None
    status: str = "draft"              # always "draft": this mock cannot send
    created_at: str


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class WorkspaceStore:
    def __init__(self, cfg: WorkspaceConfig | None = None, path: Path | str | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.cfg = cfg or WorkspaceConfig.from_config()
        self.tz = timezone(timedelta(hours=self.cfg.utc_offset_hours))
        self.path = Path(path) if path else ROOT / self.cfg.state_file
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---- persistence ----------------------------------------------------------------------------------------
    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"holds": [], "drafts": [], "idem": {}}

    def _save(self, st: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _idempotent(self, st: dict, scope: str, key: str, body: dict, make):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        hit = st["idem"].get(f"{scope}:{key}")
        if hit:
            if hit["digest"] != digest:
                raise WorkspaceError("IdempotencyKeyReuse", "idempotency key already used with a different request")
            return hit["result"]
        result = make()
        st["idem"][f"{scope}:{key}"] = {"digest": digest, "result": result}
        self._save(st)
        return result

    # ---- time helpers ---------------------------------------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock().astimezone(self.tz)

    def _parse(self, s: str) -> datetime:
        try:
            d = datetime.fromisoformat(s)
        except ValueError as e:
            raise WorkspaceError("InvalidRequest", f"not an ISO-8601 datetime: {s!r}") from e
        return d.astimezone(self.tz) if d.tzinfo else d.replace(tzinfo=self.tz)

    def _window(self, day: date) -> tuple[datetime, datetime] | None:
        if day.weekday() not in self.cfg.workdays:
            return None
        return (datetime.combine(day, _hm(self.cfg.work_start), self.tz),
                datetime.combine(day, _hm(self.cfg.work_end), self.tz))

    def _busy(self, day: date, st: dict) -> list[tuple[datetime, datetime]]:
        out = [(datetime.combine(day, _hm(r.start), self.tz), datetime.combine(day, _hm(r.end), self.tz))
               for r in self.cfg.recurring_busy if day.weekday() in r.days]
        buf = timedelta(minutes=self.cfg.buffer_min)
        for h in st["holds"]:
            if h["status"] == "tentative":
                out.append((self._parse(h["start"]) - buf, self._parse(h["end"]) + buf))
        return out

    def _is_free(self, s: datetime, e: datetime, st: dict) -> bool:
        if s < self._now() + timedelta(hours=self.cfg.min_notice_hours) or e <= s:
            return False
        if s.date() != e.date():
            return False
        win = self._window(s.date())
        if not win or s < win[0] or e > win[1]:
            return False
        return all(not (s < be and e > bs) for bs, be in self._busy(s.date(), st))

    # ---- calendar ---------------------------------------------------------------------------------------------
    def free_slots(self, date_from: str, date_to: str | None = None, duration_min: int = 30, limit: int = 20) -> list[Slot]:
        try:
            d0 = date.fromisoformat(date_from)
            d1 = date.fromisoformat(date_to) if date_to else d0
        except ValueError as e:
            raise WorkspaceError("InvalidRequest", "dates must be YYYY-MM-DD") from e
        if d1 < d0 or (d1 - d0).days >= self.cfg.max_range_days:
            raise WorkspaceError("InvalidRequest", f"range must be 0..{self.cfg.max_range_days - 1} days, from <= to")
        if not 15 <= duration_min <= 240:
            raise WorkspaceError("InvalidRequest", "duration_min must be 15..240")
        st, dur, step = self._load(), timedelta(minutes=duration_min), timedelta(minutes=self.cfg.granularity_min)
        slots: list[Slot] = []
        day = d0
        while day <= d1 and len(slots) < limit:
            win = self._window(day)
            if win:
                s = win[0]
                while s + dur <= win[1] and len(slots) < limit:
                    if self._is_free(s, s + dur, st):
                        slots.append(Slot(start=s.isoformat(), end=(s + dur).isoformat()))
                    s += step
            day += timedelta(days=1)
        return slots

    def hold_slot(self, start: str, duration_min: int, title: str, idempotency_key: str,
                  proposal_id: str | None = None) -> Hold:
        st = self._load()
        s = self._parse(start)
        body = {"start": s.isoformat(), "duration_min": duration_min, "title": title, "proposal_id": proposal_id}

        def make() -> dict:
            e = s + timedelta(minutes=duration_min)
            if not self._is_free(s, e, st):
                raise WorkspaceError("SlotUnavailable", "slot is outside working hours, too soon, or overlaps another event")
            h = Hold(hold_id=f"hold-{uuid.uuid4().hex[:8]}", title=title, start=s.isoformat(), end=e.isoformat(),
                     proposal_id=proposal_id).model_dump()
            st["holds"].append(h)
            return h
        return Hold(**self._idempotent(st, "hold", idempotency_key, body, make))

    def release_hold(self, hold_id: str) -> Hold:
        st = self._load()
        for h in st["holds"]:
            if h["hold_id"] == hold_id:
                h["status"] = "released"
                self._save(st)
                return Hold(**h)
        raise WorkspaceError("NotFound", f"hold {hold_id} not found")

    def list_holds(self, include_released: bool = False) -> list[Hold]:
        return [Hold(**h) for h in self._load()["holds"] if include_released or h["status"] == "tentative"]

    # ---- mail drafts (no send operation exists) -----------------------------------------------------------------
    def draft_email(self, to: str, subject: str, body: str, idempotency_key: str, proposal_id: str | None = None) -> EmailDraft:
        if "@" not in to or " " in to.strip():
            raise WorkspaceError("InvalidRequest", "`to` must be a single email address")
        st = self._load()
        payload = {"to": to, "subject": subject, "body": body, "proposal_id": proposal_id}

        def make() -> dict:
            d = EmailDraft(draft_id=f"draft-{uuid.uuid4().hex[:8]}", created_at=self._now().isoformat(), **payload)
            st["drafts"].append(d.model_dump())
            return d.model_dump()
        return EmailDraft(**self._idempotent(st, "draft", idempotency_key, payload, make))

    def list_drafts(self, proposal_id: str | None = None) -> list[EmailDraft]:
        return [EmailDraft(**d) for d in self._load()["drafts"] if proposal_id in (None, d.get("proposal_id"))]
