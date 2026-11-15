"""GigBid API. `uvicorn apps.api.main:app`. Everything long-lived lives in the Container (apps/api/container.py), created in the lifespan."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders

from agent.observability.context import TRACE_HEADER, set_trace_id, setup_logging

from . import errors
from .container import Container
from .deps import RateLimiter
from .routers import analytics, approvals, auth, evidence, jobs, memory, messages, portfolio, proposals, runs, system
from .service import RunService

_TRACE_OK = re.compile(r"[A-Za-z0-9_-]{8,64}")
_NO_CSP = ("/docs", "/redoc", "/openapi")


class EdgeMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware: it interferes with streaming responses). Adopts/creates the trace id for the request and
    sets security headers. The trace id is echoed so a user can quote it and an operator can find the run."""

    def __init__(self, app, secure: bool = False):
        self.app, self.secure = app, secure

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope["headers"]).get(TRACE_HEADER.encode(), b"").decode("latin-1")
        tid = set_trace_id(raw if _TRACE_OK.fullmatch(raw) else None)
        path = scope.get("path", "")

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h[TRACE_HEADER] = tid
                h["x-content-type-options"] = "nosniff"
                h["x-frame-options"] = "DENY"
                h["referrer-policy"] = "no-referrer"
                if not path.startswith(_NO_CSP):
                    h["content-security-policy"] = "default-src 'none'; frame-ancestors 'none'"
                if self.secure:
                    h["strict-transport-security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_wrapped)


def create_app(container: Container | None = None) -> FastAPI:
    c = container or Container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if c.settings.app_env != "test":
            setup_logging()
        await c.startup()
        await app.state.service.recover_stale()
        try:
            yield
        finally:
            await c.shutdown()

    app = FastAPI(title="GigBid API", version="0.1.0", lifespan=lifespan)
    app.state.container = c
    app.state.service = RunService(c)
    app.state.run_limiter = RateLimiter(c.settings.api_runs_per_minute)
    errors.install(app)
    origins = [o.strip() for o in c.settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["content-type", "last-event-id", TRACE_HEADER])
    app.add_middleware(EdgeMiddleware, secure=c.settings.cookie_secure)
    for r in (system.router, auth.router, runs.router, approvals.router, jobs.router, proposals.router, messages.router,
              memory.router, analytics.router, evidence.router, portfolio.router):
        app.include_router(r)
    return app


app = create_app()
