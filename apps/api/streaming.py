"""SSE bridge: replays a run's trace from the database and follows it live.

The database (`run_events`) is the source of truth; the in-process broker only says "look again". Reconnecting with Last-Event-ID
therefore misses nothing and repeats nothing, even across API restarts. Events: `node` (one per trace row), `status` (on change),
`done` (terminal status reached).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from sse_starlette.sse import EventSourceResponse

from .container import Container
from .service import TERMINAL, RunService


async def _events(svc: RunService, c: Container, owner: str, run_id: str, start: int) -> AsyncIterator[dict[str, Any]]:
    cursor, last_status = start, None
    q = c.broker.subscribe(run_id)  # subscribe BEFORE the first read so a wake-up cannot slip between read and wait
    try:
        while True:
            run = await svc.get_run(owner, run_id)  # status first, events second: events precede the status row they belong to
            for e in await svc.events_after(run_id, cursor):
                cursor = e.seq
                yield {"id": str(e.seq), "event": "node", "data": json.dumps(
                    {"seq": e.seq, "ts": e.ts.isoformat(), "node": e.node, "data": e.data}, default=str)}
            if run.status != last_status:
                last_status = run.status
                yield {"event": "status", "data": json.dumps({"status": run.status, "awaiting_approval": run.status == "awaiting_approval"})}
            if run.status in TERMINAL:
                yield {"event": "done", "data": json.dumps({"status": run.status})}
                return
            try:
                await asyncio.wait_for(q.get(), timeout=20)
            except TimeoutError:
                pass
    finally:
        c.broker.unsubscribe(run_id, q)


def event_stream(svc: RunService, c: Container, owner: str, run_id: str, start: int = 0) -> EventSourceResponse:
    return EventSourceResponse(_events(svc, c, owner, run_id, start), ping=15)
