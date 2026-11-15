"""Runs: start a graph run, watch it (SSE or polling), decide at the human gate, retry. Every query is scoped to the signed-in user."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from ..container import Container
from ..db import User
from ..deps import current_user, get_container, get_service, run_budget
from ..schemas import DecisionIn, EventOut, RegenerateIn, RegenerateOut, RunCreate, RunOut
from ..service import RunService
from ..streaming import event_stream

router = APIRouter(prefix="/runs", tags=["runs"])
Status = Literal["running", "awaiting_approval", "completed", "skipped", "closed", "needs_attention", "failed"]


@router.post("", response_model=RunOut, status_code=202)
async def create_run(body: RunCreate, response: Response, user: User = Depends(run_budget), svc: RunService = Depends(get_service)):
    """202 = new run started. 200 = an equivalent run is already active (double click, retry): that run is returned."""
    run, created = await svc.start(user.id, body)
    response.status_code = 202 if created else 200
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(status: Status | None = None, kind: Literal["bid", "conversation"] | None = None,
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                    user: User = Depends(current_user), svc: RunService = Depends(get_service)):
    return await svc.list_runs(user.id, status=status, kind=kind, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, user: User = Depends(current_user), svc: RunService = Depends(get_service)):
    return await svc.get_run(user.id, run_id)


@router.get("/{run_id}/trace", response_model=list[EventOut])
async def trace(run_id: str, after: int = Query(0, ge=0), user: User = Depends(current_user), svc: RunService = Depends(get_service)):
    """The execution trace as JSON (what the timeline shows). Polling alternative to the SSE stream."""
    await svc.get_run(user.id, run_id)
    return await svc.events_after(run_id, after)


@router.get("/{run_id}/events")
async def stream(run_id: str, request: Request, after: int = Query(0, ge=0), user: User = Depends(current_user),
                 svc: RunService = Depends(get_service), c: Container = Depends(get_container)):
    """Server-sent events: `node` (one per trace row), `status` (on change), `done` (terminal). Reconnect with Last-Event-ID: nothing is
    missed or repeated because the stream replays from the database, and the in-process broker only says 'look again'."""
    await svc.get_run(user.id, run_id)
    last = request.headers.get("last-event-id", "")
    return event_stream(svc, c, user.id, run_id, int(last) if last.isdigit() else after)


@router.post("/{run_id}/decision", response_model=RunOut, status_code=202)
async def decide(run_id: str, body: DecisionIn, user: User = Depends(run_budget), svc: RunService = Depends(get_service)):
    """The human gate (R4): approve / edit / reject a proposal, or approve / edit / dismiss a client reply. Exactly one decision is accepted."""
    return await svc.decide(user.id, run_id, body)


@router.post("/{run_id}/regenerate", response_model=RegenerateOut)
async def regenerate(run_id: str, body: RegenerateIn, user: User = Depends(run_budget), svc: RunService = Depends(get_service)):
    """Proposal studio: rewrite one paragraph. Returns a verified, policy-checked CANDIDATE; nothing is saved or sent. Submit it via `edit`."""
    return await svc.regenerate(user.id, run_id, body)


@router.post("/{run_id}/retry", response_model=RunOut, status_code=202)
async def retry(run_id: str, user: User = Depends(run_budget), svc: RunService = Depends(get_service)):
    return await svc.retry(user.id, run_id)
