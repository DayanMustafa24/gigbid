"""FastAPI dependencies. `current_user` is the ONLY source of `owner_id`: no endpoint takes an owner from the request body or path."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, Request

from .auth import COOKIE, read_token
from .container import Container
from .db import User
from .errors import ApiError
from .service import RunService


class RateLimiter:
    """Sliding window per key. In-process; a Redis-backed limiter replaces it in multi-instance deployments."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> float | None:
        """Record a call. Returns None if allowed, else seconds until the window frees up."""
        q, now = self._hits[key], self.clock()
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1.0, self.window - (now - q[0]))
        q.append(now)
        return None


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_service(request: Request) -> RunService:
    return request.app.state.service


def _token(request: Request) -> str | None:
    if tok := request.cookies.get(COOKIE):
        return tok
    auth = request.headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else None


async def current_user(request: Request, c: Container = Depends(get_container)) -> User:
    claims = read_token(_token(request) or "", c.settings.app_secret_key) if _token(request) else None
    if not claims:
        raise ApiError(401, "unauthenticated", "Sign in required")
    async with c.sessions() as db:
        user = await db.get(User, claims["sub"])
    if user is None:
        raise ApiError(401, "unauthenticated", "Sign in required")
    return user


async def run_budget(request: Request, user: User = Depends(current_user)) -> User:
    """Per-user limit on starting/deciding runs (every run costs model calls)."""
    wait = request.app.state.run_limiter.hit(user.id)
    if wait is not None:
        raise ApiError(429, "rate_limited", "Too many requests; slow down", retryable=True, headers={"Retry-After": str(int(wait))})
    return user
