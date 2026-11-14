"""Simulator state + rules. Pure Python (no FastAPI routing here) so it is easy to test; HTTP errors via api_error()."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from . import client_model as cm
from .config import SimConfig
from .policy_update import load_policy_update
from .sample_data import sample_world

BASE_TIME = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PUBLIC_JOB = ("job_id", "title", "required_skills", "budget", "client_id")
STATUS_BY_ISSUE = {"already_submitted": 409, "policy_violation": 422, "insufficient_connects": 402}


def api_error(status: int, code: str, message: str, headers: dict[str, str] | None = None, **extra: Any) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra}, headers)


def _hash(*parts: Any) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        raise RuntimeError(f"{path} not found: run `uv run python -m scripts.synth.build_jobs` (or GIGSIM_DATASET=sample)")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_dataset(cfg: SimConfig) -> tuple[list[dict], list[dict]]:
    if cfg.dataset == "sample":
        return sample_world()
    jobs: list[dict] = []
    if cfg.dataset in {"stream", "both"}:
        jobs += _read_jsonl(cfg.data_dir / "job_stream.jsonl")
    if cfg.dataset in {"dev", "both"}:
        for p in sorted(cfg.dev_dir.glob("dev_*.jsonl")):
            jobs += [{k: v for k, v in j.items() if k != "gold"} for j in _read_jsonl(p)]  # gold labels never enter the sim
    if not jobs:
        raise RuntimeError(f"no jobs loaded for dataset={cfg.dataset!r}")
    return jobs, _read_jsonl(cfg.data_dir / "clients.jsonl")


class World:
    def __init__(self, cfg: SimConfig, jobs: list[dict] | None = None, clients: list[dict] | None = None):
        self.cfg = cfg
        if jobs is None:
            jobs, clients = load_dataset(cfg)
        self.jobs_by_id = {j["job_id"]: j for j in jobs}
        self.clients = {c["client_id"]: c for c in (clients or [])}
        self.policy_update = load_policy_update(cfg.policy_update_file)
        self.update_active = cfg.policy_update_enabled
        self.fault_rate = cfg.fault_rate
        self.fault_after_commit_rate = cfg.fault_after_commit_rate
        self.fault_rng = random.Random(f"{cfg.seed}:faults")
        self.reset(cfg.persona)

    # ---- lifecycle -------------------------------------------------------------------------------------------
    def reset(self, persona: str | None = None) -> None:
        order = sorted(self.jobs_by_id.values(), key=lambda j: _hash(self.cfg.seed, j["job_id"]))
        self.feed = [j for j in order if persona is None or j.get("persona") == persona]
        self.persona = persona
        self.released = min(self.cfg.initial_released, len(self.feed))
        self.tick_no = 0
        self.connects = self.cfg.initial_connects
        self.proposals: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.events: list[dict] = []
        self.idem: dict[tuple[str, str], tuple[str, Any]] = {}
        self.window: deque[float] = deque()
        self._refresh_visible()

    def _refresh_visible(self) -> None:
        self.visible = {j["job_id"] for j in self.feed[: self.released]}

    def now_iso(self) -> str:
        return (BASE_TIME + timedelta(hours=self.tick_no)).isoformat().replace("+00:00", "Z")

    # ---- request guards -------------------------------------------------------------------------------------
    def check_auth(self, authorization: str | None) -> None:
        if self.cfg.token and authorization != f"Bearer {self.cfg.token}":
            raise api_error(401, "unauthorized", "Missing or invalid bearer token", {"WWW-Authenticate": "Bearer"})

    def rate_limit(self) -> None:
        now, w = self.cfg.clock(), self.window
        while w and now - w[0] >= 60:
            w.popleft()
        if len(w) >= self.cfg.rate_limit_per_min:
            retry = max(1, math.ceil(60 - (now - w[0]))) if w else 1
            raise api_error(429, "rate_limited", f"Rate limit exceeded; retry in {retry}s", {"Retry-After": str(retry)})
        w.append(now)

    def maybe_fault(self, rate: float) -> None:
        if rate > 0 and self.fault_rng.random() < rate:
            status = self.fault_rng.choice((500, 502, 503))
            raise api_error(status, "upstream_error", "Temporary upstream error", {"Retry-After": "1"})

    # ---- policy / costs ---------------------------------------------------------------------------------------
    def connects_cost(self, job: dict) -> int:
        return int(job["connects_cost"]) + (self.policy_update.connects_surcharge if self.update_active else 0)

    def platform_policy(self) -> dict:
        u, on = self.policy_update, self.update_active
        return {"version": u.id if on else "gigsim-local-base", "update_active": on,
                "connects_surcharge": u.connects_surcharge if on else 0,
                "requires_ai_disclosure": u.require_ai_disclosure if on else False,
                "effective": u.effective if on else None, "provenance": u.provenance,
                "notes": list(u.notes) if on else []}

    # ---- reads --------------------------------------------------------------------------------------------------
    def public_job(self, j: dict, full: bool = False) -> dict:
        d = {k: j[k] for k in PUBLIC_JOB} | {"connects_cost": self.connects_cost(j), "snippet": j["description"][:240]}
        if full:
            d |= {"description": j["description"], "screening_questions": j["screening_questions"], "duration_days": j["duration_days"]}
        return d

    def get_job(self, job_id: str) -> dict:
        if job_id not in self.visible:
            raise api_error(404, "not_found", f"job {job_id} not found")
        return self.public_job(self.jobs_by_id[job_id], full=True)

    def search(self, q: str, limit: int, offset: int) -> dict:
        toks = re.findall(r"[a-z0-9+#.]+", q.lower())
        rows = []
        for pos, j in enumerate(self.feed[: self.released]):
            hay = f"{j['title']} {j['description']} {' '.join(j['required_skills'])}".lower()
            score = sum(t in hay for t in toks)
            if toks and score == 0:
                continue
            rows.append((score, pos, j))
        rows.sort(key=lambda r: (-r[0], -r[1]))            # best match first, then newest
        page = rows[offset: offset + limit]
        return {"items": [self.public_job(j) for _, _, j in page], "total": len(rows), "released": self.released}

    def client_history(self, client_id: str) -> dict:
        c = self.clients.get(client_id)
        if c is None:
            raise api_error(404, "not_found", f"client {client_id} not found")
        return {k: c[k] for k in ("client_id", "payment_verified", "completed_projects", "average_rating", "total_spent", "member_since_days")}

    def proposal(self, pid: str) -> dict:
        if pid not in self.proposals:
            raise api_error(404, "not_found", f"proposal {pid} not found")
        return self.proposals[pid]

    @staticmethod
    def public_proposal(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.startswith("_")}

    def list_messages(self, pid: str) -> list[dict]:
        self.proposal(pid)
        return [m for m in self.messages if m["proposal_id"] == pid]

    # ---- idempotent writes --------------------------------------------------------------------------------
    def idempotent(self, scope: str, key: str | None, body: Any, fn):
        if not key:
            raise api_error(400, "idempotency_key_required", "Idempotency-Key header is required for writes")
        digest = _hash(json.dumps(body, sort_keys=True, default=str))
        hit = self.idem.get((scope, key))
        if hit:
            if hit[0] != digest:
                raise api_error(409, "idempotency_key_reuse", "Idempotency-Key was already used with a different request")
            return hit[1], True
        result = fn()
        self.idem[(scope, key)] = (digest, result)
        return result, False

    def create_proposal(self, body: dict, key: str | None) -> tuple[dict, bool]:
        def fn() -> dict:
            if body["job_id"] not in self.visible:
                raise api_error(404, "not_found", f"job {body['job_id']} not found")
            if any(p["job_id"] == body["job_id"] for p in self.proposals.values()):
                raise api_error(409, "duplicate_proposal", "a proposal for this job already exists")
            job = self.jobs_by_id[body["job_id"]]
            pid = f"prop-{len(self.proposals) + 1:05d}"
            p = {"proposal_id": pid, "job_id": job["job_id"], "status": "draft", "cover_letter": body["cover_letter"],
                 "screening_answers": body["screening_answers"], "bid_amount": body["bid_amount"], "bid_type": body["bid_type"],
                 "ai_disclosure": body["ai_disclosure"], "connects_cost": self.connects_cost(job), "created_at": self.now_iso(),
                 "connects_spent": 0}
            self.proposals[pid] = p
            return self.public_proposal(p)
        return self.idempotent("create", key, body, fn)

    def _issues(self, p: dict) -> tuple[int, list[dict]]:
        cost, issues = self.connects_cost(self.jobs_by_id[p["job_id"]]), []
        if p["status"] != "draft":
            issues.append({"code": "already_submitted", "message": f"proposal is {p['status']}"})
        if self.update_active and self.policy_update.require_ai_disclosure and not p["ai_disclosure"]:
            issues.append({"code": "policy_violation", "rule": "PU-ai-disclosure",
                           "message": "platform policy update requires ai_disclosure=true"})
        if self.connects < cost:
            issues.append({"code": "insufficient_connects", "message": f"need {cost} Connects, have {self.connects}"})
        return cost, issues

    def preview_submit(self, pid: str) -> dict:
        p = self.proposal(pid)
        cost, issues = self._issues(p)
        return {"dry_run": True, "proposal_id": pid, "would_spend": cost, "balance_before": self.connects,
                "balance_after": self.connects - cost, "submittable": not issues, "issues": issues}

    def submit(self, pid: str, key: str | None) -> tuple[dict, bool]:
        p = self.proposal(pid)

        def fn() -> dict:
            cost, issues = self._issues(p)
            if issues:
                i = issues[0]
                raise api_error(STATUS_BY_ISSUE[i["code"]], i["code"], i["message"], rule=i.get("rule"))
            self.connects -= cost
            p.update(status="submitted", connects_spent=cost, submitted_tick=self.tick_no)
            self.events.append({"due": self.tick_no + self.cfg.reply_delay_ticks, "kind": "initial", "pid": pid})
            return {"proposal_id": pid, "status": "submitted", "connects_spent": cost, "balance_after": self.connects}
        return self.idempotent(f"submit:{pid}", key, {"pid": pid}, fn)

    def send_message(self, body: dict, key: str | None) -> tuple[dict, bool]:
        p = self.proposal(body["proposal_id"])

        def fn() -> dict:
            if p["status"] in {"draft", "ignored", "closed", "hired"}:
                raise api_error(409, "conversation_closed", f"cannot message while proposal is {p['status']}")
            m = self._add_message(p["proposal_id"], "freelancer", body["text"])
            self.events.append({"due": self.tick_no + 1, "kind": "followup", "pid": p["proposal_id"], "mid": m["message_id"]})
            return m
        return self.idempotent(f"message:{body['proposal_id']}", key, body, fn)

    def _add_message(self, pid: str, sender: str, text: str) -> dict:
        m = {"message_id": f"msg-{len(self.messages) + 1:05d}", "proposal_id": pid, "sender": sender, "text": text,
             "sent_at": self.now_iso()}
        self.messages.append(m)
        return m

    # ---- simulation clock -----------------------------------------------------------------------------------
    def tick(self, n: int = 1) -> None:
        for _ in range(n):
            self.tick_no += 1
            self.released = min(len(self.feed), self.released + self.cfg.release_per_tick)
            due = [e for e in self.events if e["due"] <= self.tick_no]
            self.events = [e for e in self.events if e["due"] > self.tick_no]
            for e in due:
                self._process(e)
        self._refresh_visible()

    def _process(self, e: dict) -> None:
        p = self.proposals[e["pid"]]
        job = self.jobs_by_id[p["job_id"]]
        client = self.clients.get(job["client_id"], {})
        prefs = cm.hidden_prefs(self.cfg.seed, job["client_id"], job)
        rng = random.Random(_hash(self.cfg.seed, p["proposal_id"], e["kind"], e.get("mid", "")))
        if e["kind"] == "initial":
            score, factors = cm.score_proposal(prefs, job, p)
            p["_score"], p["_factors"] = score, factors
            if cm.is_scammer(client):
                p["status"] = "replied"
                self._add_message(p["proposal_id"], "client", cm.compose_reply("lure", prefs, job, p, rng))
            elif cm.decide_initial(score, rng):
                p["status"] = "replied"
                self._add_message(p["proposal_id"], "client", cm.compose_reply("reply", prefs, job, p, rng, self.cfg.client_injection_rate))
            else:
                p["status"] = "ignored"
            return
        if cm.is_scammer(client):
            self._add_message(p["proposal_id"], "client", cm.compose_reply("lure", prefs, job, p, rng))
            return
        text = next(m["text"] for m in self.messages if m["message_id"] == e["mid"])
        outcome = cm.decide_followup(p["status"], p["_score"], text, rng)
        p["status"] = outcome
        self._add_message(p["proposal_id"], "client", cm.compose_reply(outcome, prefs, job, p, rng, self.cfg.client_injection_rate))

    # ---- admin (evals / demos only; never exposed through the marketplace port) ---------------------------------
    def outcomes(self) -> list[dict]:
        out = []
        for p in self.proposals.values():
            job = self.jobs_by_id[p["job_id"]]
            prefs = cm.hidden_prefs(self.cfg.seed, job["client_id"], job)
            out.append({"proposal_id": p["proposal_id"], "job_id": p["job_id"], "client_id": job["client_id"],
                        "status": p["status"], "connects_spent": p["connects_spent"], "score": p.get("_score"),
                        "factors": p.get("_factors"), "hidden_prefs": asdict(prefs)})
        return out
