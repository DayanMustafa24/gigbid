"""Run service: starts graph runs, streams their trace into the database, pauses at the human gate, resumes on the human's decision.

Design rules (each one is tested):
  * A run is one LangGraph thread. `runs` holds status + the approval request; `run_events` is the replayable trace (SSE reads it).
  * Executing is a background task; the HTTP call returns at once. The graph checkpoint (not memory) is the source of truth, so a
    restart loses nothing: stale `running` runs become `failed` and `retry` resumes them from their last checkpoint.
  * Exactly one decision per approval: the resume is a compare-and-set on status (`awaiting_approval` -> `running`).
  * Retry of a failed submission/send re-runs the deterministic executor/sender (same idempotency keys), never the LLM nodes.
  * Everything is scoped by `owner_id`; another user's run is a 404, not a 403.
  * Long-term memory is written here, after a run finishes (agent/memory/service.py), never from inside a graph.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select, update

from agent.approvals import ApprovalGrant, text_sha
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.marketplace_port import MarketplaceError
from agent.memory.service import learn_from_run
from agent.nodes.conversation_gates import message_sender
from agent.nodes.gates import executor
from agent.nodes.regenerate import RegenerateError, regenerate_paragraph
from agent.observability.context import set_trace_id
from agent.policy.engine import load_policy
from agent.runtime import list_personas, persona_visible
from agent.security.pii import mask, mask_obj

from .container import Container
from .db import Run, RunEvent, new_id, utcnow
from .errors import ApiError, from_marketplace
from .schemas import DecisionIn, RegenerateIn, RunCreate

log = logging.getLogger("gigbid.service")

ACTIVE = ("running", "awaiting_approval")
TERMINAL = ("completed", "skipped", "closed", "needs_attention", "failed")
RECURSION_LIMIT = 60  # LangGraph supersteps; the product budget (budgets.max_graph_steps) stops runs long before this
STALE_AFTER = timedelta(minutes=10)  # a `running` run untouched this long belongs to a dead process
OUTCOME_OF_STATUS = {"hired": "won", "replied": "replied", "interview": "replied", "ignored": "ignored", "closed": "ignored"}


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def pending_of(snap: Any) -> dict[str, Any] | None:
    for task in getattr(snap, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            v = getattr(intr, "value", intr)
            return jsonable(v) if isinstance(v, dict) else {"kind": "unknown", "value": jsonable(v)}
    return None


def status_of(values: dict[str, Any], kind: str) -> str:
    a = values.get("approval_status")
    if values.get("needs_attention") or a in ("submission_failed", "send_failed", "submission_blocked", "blocked_after_edit"):
        return "needs_attention"
    if a in ("rejected", "dismissed"):
        return "closed"
    if kind == "bid" and values.get("decision") in ("skip", "watch") and not a:
        return "skipped"
    return "completed"


def summarise(values: dict[str, Any], kind: str) -> dict[str, Any]:
    job = values.get("job") or {}
    detail, risk, fit = values.get("decision_detail") or {}, values.get("risk_report") or {}, values.get("fit_report") or {}
    ver, inj, draft = values.get("verification_report") or {}, values.get("injection") or {}, values.get("proposal_draft") or {}
    s: dict[str, Any] = {
        "decision": values.get("decision"), "reasons": detail.get("reasons", []), "confidence": detail.get("confidence"),
        "risk": {"score": risk.get("score"), "flags": risk.get("flags", []), "explanation": risk.get("explanation", "")} if risk else None,
        "fit": {"score": fit.get("score"), "matched_skills": fit.get("matched_skills", []), "missing_skills": fit.get("missing_skills", []),
                "matches": fit.get("matches", []), "reasons": fit.get("reasons", [])} if fit else None,
        "job_spec": {k: (values.get("job_spec") or {}).get(k) for k in ("requirements", "deliverables", "hidden_needs")} if values.get("job_spec") else None,
        "price": values.get("price_quote"),
        "injection": {"level": inj.get("level"), "score": inj.get("score")} if inj else None,
        "verification": {"unsupported_before": ver.get("unsupported_before"), "factual_claims_before": ver.get("factual_claims_before"),
                         "unsupported_after": ver.get("unsupported_after")} if ver else None,
        "strategy": draft.get("strategy"), "revisions": values.get("revision", 0),
        "approval_status": values.get("approval_status"), "submission": values.get("submission"), "sent_message": values.get("sent_message"),
        "needs_attention": bool(values.get("needs_attention")), "dead_letter": values.get("dead_letter"),
        "usage": values.get("usage"), "steps": values.get("steps"), "errors": list(values.get("errors", []))[-5:],
    }
    if job:
        s["job"] = {"title": job.get("title"), "category": job.get("category"), "description": str(job.get("description", ""))[:3000], "budget": job.get("budget"),
                    "required_skills": job.get("required_skills", []), "client_id": job.get("client_id"), "manual": bool(job.get("manual"))}
    if kind == "conversation":
        conv = values.get("conversation") or {}
        s["conversation"] = {"intent": conv.get("intent"), "reason": conv.get("reason")}
    return jsonable(s)


def grant_for_decision(run: Run, owner: str, action: str) -> ApprovalGrant | None:
    """The ONLY place approval authority is created (R4): at the moment a human approves or edits. Reject/dismiss grant nothing.
    An unedited approval is bound to the exact text the human saw; an edit is the human's own text (re-checked by the policy guard)."""
    if action not in ("approve", "edit"):
        return None
    pending = run.pending or {}
    shown = (pending.get("draft") or {}).get("cover_letter") if run.kind == "bid" else pending.get("draft_reply")
    return ApprovalGrant(approver=owner, run_id=run.id, approved_sha=text_sha(shown) if action == "approve" and shown else None)


def grant_from_state(run: Run, owner: str, values: dict[str, Any]) -> ApprovalGrant | None:
    """Retry of a failed write: the human already decided, and the decision is recorded in the checkpointed state. No decision, no grant."""
    a = values.get("approval") or {}
    if a.get("status") not in ("approve", "edit"):
        return None
    original = a.get("original_text")
    return ApprovalGrant(approver=owner, run_id=run.id, approved_sha=text_sha(original) if a["status"] == "approve" and original else None)


class RunService:
    def __init__(self, c: Container) -> None:
        self.c = c
        self._start_lock = asyncio.Lock()

    # ---- helpers ----------------------------------------------------------------------------------------------------------------------
    def persona_ok(self, persona: str, owner: str | None = None) -> bool:
        if persona.startswith("user-") and (owner is None or not persona_visible(persona, owner)):
            return False  # someone else's uploaded portfolio is indistinguishable from a missing one
        return bool(self.c.deps_factory) or persona in list_personas(self.c.cfg)

    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self.c.tasks.add(t)
        t.add_done_callback(self.c.tasks.discard)

    async def drain(self) -> None:
        """Wait for every background run to settle (tests, graceful shutdown)."""
        while self.c.tasks:
            await asyncio.gather(*list(self.c.tasks), return_exceptions=True)

    def _graph(self, kind: str, deps: Any) -> Any:
        build = build_bid_graph if kind == "bid" else build_conversation_graph
        return build(deps, self.c.checkpointer)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}

    # ---- queries ----------------------------------------------------------------------------------------------------------------------
    async def get_run(self, owner: str, run_id: str) -> Run:
        async with self.c.sessions() as db:
            run = (await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == owner))).scalar_one_or_none()
        if run is None:
            raise ApiError(404, "run_not_found", "Run not found")
        return run

    async def list_runs(self, owner: str, *, status: str | None = None, kind: str | None = None, limit: int = 50, offset: int = 0) -> list[Run]:
        q = select(Run).where(Run.owner_id == owner)
        if status:
            q = q.where(Run.status == status)
        if kind:
            q = q.where(Run.kind == kind)
        async with self.c.sessions() as db:
            return list((await db.execute(q.order_by(Run.created_at.desc(), Run.id).limit(limit).offset(offset))).scalars())

    async def events_after(self, run_id: str, after: int = 0, limit: int = 500) -> list[RunEvent]:
        async with self.c.sessions() as db:
            return list((await db.execute(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after)
                                          .order_by(RunEvent.seq).limit(limit))).scalars())

    async def _add_events(self, run_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
        if not items:
            return
        async with self.c.sessions() as db:
            seq = (await db.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))).scalar() or 0
            for node, data in items:
                seq += 1
                db.add(RunEvent(run_id=run_id, seq=seq, node=node, data=jsonable(mask_obj(data))))
            await db.execute(update(Run).where(Run.id == run_id).values(updated_at=utcnow()))
            await db.commit()
        self.c.broker.publish(run_id)

    async def _cas(self, run_id: str, owner: str, from_status: tuple[str, ...], to: str) -> bool:
        async with self.c.sessions() as db:
            res = await db.execute(update(Run).where(Run.id == run_id, Run.owner_id == owner, Run.status.in_(from_status))
                                   .values(status=to, error=None, pending=None, updated_at=utcnow()))
            await db.commit()
            return res.rowcount == 1

    async def _known_jobs(self, owner: str, persona: str, exclude: str) -> list[dict[str, Any]]:
        """Jobs this user already ran: the Risk Assessor flags a near-identical repost of one of them as a duplicate."""
        async with self.c.sessions() as db:
            rows = (await db.execute(select(Run.subject_id, Run.summary).where(Run.owner_id == owner, Run.persona == persona, Run.kind == "bid",
                                                                              Run.subject_id != exclude)
                                     .order_by(Run.created_at.desc()).limit(200))).all()
        return [{"job_id": sid, "description": (s or {}).get("job", {}).get("description", "")} for sid, s in rows if (s or {}).get("job")]

    # ---- start ------------------------------------------------------------------------------------------------------------------------
    async def _resolve_job(self, body: RunCreate) -> dict[str, Any]:
        if body.job is not None:
            j = body.job
            jid = "manual-" + hashlib.sha256(f"{j.title}\n{j.description}".encode()).hexdigest()[:12]
            connects = j.connects_cost or int(load_policy().costs.get("connects_per_proposal_default", 8))
            return {"job_id": jid, "client_id": "", "title": j.title, "description": j.description, "required_skills": list(j.required_skills),
                    "screening_questions": list(j.screening_questions), "duration_days": None, "category": None, "connects_cost": connects,
                    "budget": {"min": j.budget_min, "max": j.budget_max, "type": j.budget_type, "currency": j.currency.upper()}, "manual": True}
        if self.c.market is None:
            raise ApiError(503, "marketplace_unavailable", "No marketplace is configured", retryable=True)
        try:
            return (await self.c.market.get_job(body.job_id or "")).model_dump()
        except MarketplaceError as e:
            raise from_marketplace(e) from e

    async def start(self, owner: str, body: RunCreate) -> tuple[Run, bool]:
        """Returns (run, created). Starting the same job (or the same client message) again while a run is active returns that run."""
        if not self.persona_ok(body.persona, owner):
            raise ApiError(422, "unknown_persona", f"Unknown persona {body.persona!r}")
        if body.kind == "bid":
            job = await self._resolve_job(body)
            subject, payload, extra = job["job_id"], {"job": job}, {}
        else:
            sha = hashlib.sha256((body.client_message or "").encode()).hexdigest()[:16]
            subject, extra = body.proposal_id or "", {"proposal_id": body.proposal_id, "message_sha": sha}
            payload = {"proposal_id": subject, "client_message": body.client_message, "persona": body.persona}
        async with self._start_lock:
            async with self.c.sessions() as db:
                live = (await db.execute(select(Run).where(Run.owner_id == owner, Run.kind == body.kind, Run.subject_id == subject,
                                                           Run.persona == body.persona, Run.status.in_(ACTIVE))
                                         .order_by(Run.created_at.desc()))).scalars().all()
                same = next((r for r in live if body.kind == "bid" or (r.summary or {}).get("message_sha") == extra["message_sha"]), None)
                if same is not None:
                    return same, False
                run = Run(owner_id=owner, kind=body.kind, subject_id=subject, persona=body.persona, thread_id=f"{body.kind}-{new_id()}",
                          status="running", summary=extra)
                db.add(run)
                await db.commit()
        deps = await self.c.deps_for(body.persona, owner)
        if body.kind == "bid":
            known = await self._known_jobs(owner, body.persona, exclude=subject)
            deps.known_jobs = lambda known=known: known
        await self._add_events(run.id, [("run_started", {"kind": body.kind, "subject": subject, "persona": body.persona})])
        self._spawn(self._drive(run.id, owner, run.kind, run.thread_id, self._graph(run.kind, deps), payload))
        return run, True

    # ---- drive ------------------------------------------------------------------------------------------------------------------------
    async def _drive(self, run_id: str, owner: str, kind: str, thread_id: str, graph: Any, payload: Any, *, pre: Any = None) -> None:
        set_trace_id(run_id)  # the run id is the trace id: UI -> API -> graph -> marketplace -> audit all share it
        config = self._config(thread_id)
        try:
            if pre is not None:
                await pre(graph, config)
            else:
                async for chunk in graph.astream(payload, config, stream_mode="updates"):
                    await self._persist(run_id, chunk)
            await self._finish(run_id, owner, kind, await graph.aget_state(config))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("run %s failed", run_id)
            await self._fail(run_id, f"{type(e).__name__}: {mask(str(e))[:400]}")

    async def _persist(self, run_id: str, chunk: dict[str, Any]) -> None:
        items: list[tuple[str, dict[str, Any]]] = []
        for node, upd in chunk.items():
            if node == "__interrupt__":
                first = next(iter(upd), None)
                v = getattr(first, "value", None)
                items.append(("interrupt", {"kind": v.get("kind") if isinstance(v, dict) else None}))
                continue
            for ev in (upd or {}).get("events") or []:
                items.append((str(ev.get("node", node)), {k: v for k, v in ev.items() if k != "node"}))
        await self._add_events(run_id, items)

    async def _finish(self, run_id: str, owner: str, kind: str, snap: Any) -> None:
        values = dict(snap.values or {})
        pending = pending_of(snap)
        status = "awaiting_approval" if pending else status_of(values, kind)
        async with self.c.sessions() as db:
            run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
            summary = {**(run.summary or {}), **{k: v for k, v in summarise(values, kind).items() if v is not None}}
        learned: dict[str, Any] = {}
        if status != "awaiting_approval" and kind == "bid":
            try:  # style memory learns only from the human's decision, after the run (R7)
                learned = await asyncio.to_thread(learn_from_run, self.c.memory, owner, values)
            except Exception:
                log.exception("memory update failed for run %s", run_id)
        await self._add_events(run_id, [("run_status", {"status": status, "memory": learned or None})])
        async with self.c.sessions() as db:
            await db.execute(update(Run).where(Run.id == run_id).values(
                status=status, decision=values.get("decision"), summary=summary, pending=pending, error=None, updated_at=utcnow()))
            await db.commit()
        self.c.broker.publish(run_id)  # after the status row is final: SSE readers see status and events consistently

    async def _fail(self, run_id: str, error: str) -> None:
        await self._add_events(run_id, [("run_status", {"status": "failed", "error": error})])
        async with self.c.sessions() as db:
            await db.execute(update(Run).where(Run.id == run_id).values(status="failed", error=error, updated_at=utcnow()))
            await db.commit()
        self.c.broker.publish(run_id)

    # ---- human decision -----------------------------------------------------------------------------------------------------------------
    async def decide(self, owner: str, run_id: str, body: DecisionIn) -> Run:
        run = await self.get_run(owner, run_id)
        if run.status != "awaiting_approval" or not run.pending:
            raise ApiError(409, "not_awaiting_approval", f"Run is {run.status}; nothing to decide")
        allowed = list(run.pending.get("actions", []))
        if body.action not in allowed:
            raise ApiError(422, "action_not_allowed", f"This request allows only: {', '.join(allowed)}")
        resume: dict[str, Any] = {"status": body.action}
        if body.action == "edit":
            resume["edited_text"] = body.edited_text
        if body.reason:
            resume["reason"] = body.reason
        if not await self._cas(run_id, owner, ("awaiting_approval",), "running"):  # exactly one decision wins
            raise ApiError(409, "already_decided", "This request was already decided")
        await self._add_events(run_id, [("human_decision", {"action": body.action, "edited": body.action == "edit", "by": owner})])
        deps = await self.c.deps_for(run.persona, owner)
        deps = dataclasses.replace(deps, approval=grant_for_decision(run, owner, body.action))
        self._spawn(self._drive(run_id, owner, run.kind, run.thread_id, self._graph(run.kind, deps), Command(resume=resume)))
        return await self.get_run(owner, run_id)

    # ---- retry ------------------------------------------------------------------------------------------------------------------------
    async def regenerate(self, owner: str, run_id: str, body: RegenerateIn) -> dict[str, Any]:
        """Candidate rewrite of one paragraph of a pending proposal. Read-only on the run: the human submits it via the normal `edit` decision."""
        run = await self.get_run(owner, run_id)
        if run.kind != "bid" or run.status != "awaiting_approval" or (run.pending or {}).get("kind") != "proposal_approval":
            raise ApiError(409, "not_awaiting_approval", "Only a proposal that is waiting for your decision can be regenerated")
        deps = await self.c.deps_for(run.persona, owner)
        snap = await self._graph(run.kind, deps).aget_state(self._config(run.thread_id))
        try:
            return await regenerate_paragraph(dict(snap.values or {}), deps, body.paragraph, body.instruction)
        except RegenerateError as e:
            status = {"paragraph_out_of_range": 422, "model_unavailable": 503, "model_failed": 502, "nothing_supported": 422}.get(e.code, 422)
            raise ApiError(status, e.code, str(e), retryable=e.code in ("model_unavailable", "model_failed")) from e

    # ---- retry ------------------------------------------------------------------------------------------------------------------------
    async def retry(self, owner: str, run_id: str) -> Run:
        """`failed` (crash/restart): resume from the last checkpoint. `needs_attention` after a failed submission/send: re-run the deterministic
        executor/sender with the same idempotency keys. A dead-lettered run (budget, policy block) is not retried: start a new one."""
        run = await self.get_run(owner, run_id)
        deps = await self.c.deps_for(run.persona, owner)
        graph, config = self._graph(run.kind, deps), self._config(run.thread_id)
        if run.status not in ("failed", "needs_attention"):
            raise ApiError(409, "not_retryable", f"Run is {run.status}")
        snap = await graph.aget_state(config)
        values = dict(snap.values or {})
        deps = dataclasses.replace(deps, approval=grant_from_state(run, owner, values))
        graph = self._graph(run.kind, deps)
        a = values.get("approval_status")
        fn = executor if (run.kind == "bid" and a == "submission_failed") else message_sender if (run.kind == "conversation" and a == "send_failed") else None
        if run.status == "failed" and snap.next:
            pre = None  # resume the interrupted superstep
        elif fn is not None:
            async def pre(g: Any, cfg: dict[str, Any], fn=fn, values=values, deps=deps) -> None:
                upd = await fn(values, deps)
                upd["needs_attention"] = bool(upd.get("needs_attention", False))  # cleared when the retry succeeds
                await g.aupdate_state(cfg, upd, as_node="executor" if run.kind == "bid" else "message_sender")
                await self._add_events(run_id, [(upd["events"][0]["node"], {k: v for k, v in upd["events"][0].items() if k != "node"})])
        else:
            raise ApiError(409, "not_retryable", "Nothing to retry: start a new run after fixing the cause")
        if not await self._cas(run_id, owner, ("failed", "needs_attention"), "running"):
            raise ApiError(409, "already_running", "Run is already being retried")
        await self._add_events(run_id, [("retry", {"by": owner})])
        self._spawn(self._drive(run_id, owner, run.kind, run.thread_id, graph, None, pre=pre))
        return await self.get_run(owner, run_id)

    # ---- housekeeping -----------------------------------------------------------------------------------------------------------------
    async def recover_stale(self) -> int:
        """Runs stuck in `running` with no activity belong to a crashed process: mark failed so `retry` can resume them from the checkpoint."""
        cutoff = utcnow() - STALE_AFTER
        async with self.c.sessions() as db:
            res = await db.execute(update(Run).where(Run.status == "running", Run.updated_at < cutoff)
                                   .values(status="failed", error="interrupted (process restarted); use retry", updated_at=utcnow()))
            await db.commit()
            return res.rowcount or 0

    # ---- proposals / outcomes ---------------------------------------------------------------------------------------------------------
    async def proposal_runs(self, owner: str, limit: int = 500) -> list[Run]:
        runs = await self.list_runs(owner, kind="bid", limit=limit)
        return [r for r in runs if ((r.summary or {}).get("submission") or {}).get("proposal_id")]

    async def sync_outcomes(self, owner: str) -> dict[str, int]:
        """Marketplace status of every submitted proposal -> outcome memory (X2 experiments, measured pricing rates). Forward-only."""
        if self.c.market is None:
            raise ApiError(503, "marketplace_unavailable", "No marketplace is configured", retryable=True)
        stats = {"checked": 0, "recorded": 0, "errors": 0}
        for r in await self.proposal_runs(owner):
            pid = r.summary["submission"]["proposal_id"]
            stats["checked"] += 1
            try:
                prop = await self.c.market.get_proposal(pid)
            except MarketplaceError:
                stats["errors"] += 1
                continue
            outcome = OUTCOME_OF_STATUS.get(prop.status)
            if outcome:
                await asyncio.to_thread(self.c.memory.record_outcome, owner, r.persona, pid, r.summary.get("strategy"), outcome,
                                       "marketplace", (r.summary.get("job") or {}).get("category"))
                stats["recorded"] += 1
        return stats
