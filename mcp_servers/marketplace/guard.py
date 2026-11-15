"""Cross-cutting guards for the Marketplace MCP server: rate limiter, hash-chained audit log, bearer-auth middleware.

Pure Python / ASGI: no MCP imports, so each piece is unit-tested directly.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agent.observability.context import TRACE_HEADER, get_trace_id, set_trace_id
from agent.security.pii import mask_obj

USER_HEADER = "x-gigbid-user"
_caller: contextvars.ContextVar[str] = contextvars.ContextVar("caller", default="anonymous")


def current_caller() -> str:
    return _caller.get()


class ToolRateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limit exceeded; retry in {retry_after:.1f}s")
        self.retry_after = retry_after


class RateLimiter:
    """Sliding window per key (per caller, per read/write class)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute, self._clock = per_minute, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now, q = self._clock(), self._hits[key]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise ToolRateLimited(60 - (now - q[0]))
        q.append(now)


class AuditLog:
    """Append-only JSONL. Each record carries sha256(prev_hash + body), so any edit or deletion is detectable."""

    GENESIS = "0" * 64

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last = self.GENESIS
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._last = json.loads(line)["hash"]

    def append(self, **record) -> dict:
        with self._lock:
            body = {"ts": datetime.now(UTC).isoformat(), "trace_id": get_trace_id(), "caller": current_caller(),
                    **mask_obj(record), "prev": self._last}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            body["hash"] = digest
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(body, default=str) + "\n")
            self._last = digest
            return body

    def verify(self) -> bool:
        prev = self.GENESIS
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            claimed = rec.pop("hash")
            if rec["prev"] != prev or hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest() != claimed:
                return False
            prev = claimed
        return True


class BearerAuthMiddleware:
    """Pure ASGI: rejects requests without the shared bearer token (401), and binds trace id + caller for the request."""

    def __init__(self, app, token: str | None, exempt: tuple[str, ...] = ("/healthz",)):
        self.app, self.token, self.exempt = app, token, exempt

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt:
            if scope["type"] == "http" and scope["path"] in self.exempt:
                return await _respond(send, 200, b'{"status":"ok"}')
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if self.token:
            supplied = headers.get("authorization", "")
            if not (supplied.lower().startswith("bearer ")
                    and hmac.compare_digest(supplied[7:].strip(), self.token)):
                return await _respond(send, 401, b'{"error":"missing or invalid bearer token"}',
                                      [(b"www-authenticate", b"Bearer")])
        set_trace_id(headers.get(TRACE_HEADER))
        _caller.set(headers.get(USER_HEADER, "anonymous"))
        return await self.app(scope, receive, send)


async def _respond(send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), *(extra or [])]})
    await send({"type": "http.response.body", "body": body})
