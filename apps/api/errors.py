"""One error shape for the whole API: {"error": {"code", "message", "retryable", "details?"}}. The web app keys its UI on `code`
(e.g. `marketplace_unavailable` -> the degraded-mode banner), never on message text. Internal errors never leak details."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent.marketplace_port import (
    AuthError,
    Conflict,
    InsufficientConnects,
    InvalidRequest,
    MarketplaceError,
    NotFound,
    PolicyRejected,
    RateLimited,
    Unavailable,
)

log = logging.getLogger("gigbid.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable, self.headers = status, code, message, retryable, headers or {}


def error_body(code: str, message: str, retryable: bool = False, details: list[dict] | None = None) -> dict:
    out: dict = {"error": {"code": code, "message": message, "retryable": retryable}}
    if details:
        out["error"]["details"] = details
    return out


def from_marketplace(e: MarketplaceError) -> ApiError:
    """Port errors -> HTTP. Unavailable/RateLimited are the 'marketplace down' states the UI shows as a banner."""
    if isinstance(e, RateLimited):
        return ApiError(503, "marketplace_unavailable", "The marketplace is rate limiting us; try again shortly", retryable=True,
                        headers={"Retry-After": str(max(1, int(e.retry_after)))})
    if isinstance(e, Unavailable):
        return ApiError(503, "marketplace_unavailable", "The marketplace is unavailable", retryable=True)
    if isinstance(e, NotFound):
        return ApiError(404, "not_found", str(e) or "Not found on the marketplace")
    if isinstance(e, InsufficientConnects):
        return ApiError(409, "insufficient_connects", str(e))
    if isinstance(e, Conflict):
        return ApiError(409, "conflict", str(e))
    if isinstance(e, (InvalidRequest, PolicyRejected)):
        return ApiError(422, "marketplace_rejected", str(e))
    if isinstance(e, AuthError):
        return ApiError(502, "marketplace_auth", "The marketplace rejected our credentials")
    return ApiError(502, "marketplace_error", "The marketplace returned an error")


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(error_body(e.code, e.message, e.retryable), status_code=e.status, headers=e.headers)

    @app.exception_handler(MarketplaceError)
    async def _market(r: Request, e: MarketplaceError) -> JSONResponse:
        return await _api(r, from_marketplace(e))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError) -> JSONResponse:
        details = [{"loc": ".".join(str(x) for x in err.get("loc", ())), "msg": str(err.get("msg", ""))} for err in e.errors()]
        return JSONResponse(error_body("validation_error", "Request validation failed", details=details), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")  # details go to the (PII-masked) log, never to the client
        return JSONResponse(error_body("internal_error", "Unexpected error"), status_code=500)
