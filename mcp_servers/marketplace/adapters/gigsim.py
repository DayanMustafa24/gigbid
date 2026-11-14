"""GigSim adapter: implements agent.marketplace_port.MarketplacePort over the simulator's REST API.

Transport concerns live here, nowhere else: bearer auth, client-side politeness (sliding-window throttle below the
platform limit), bounded retries with exponential backoff + jitter, Retry-After handling, and error mapping.
Writes are always retried with the SAME Idempotency-Key, so a response lost after commit never double-spends.
If the instructor's GigSim API differs from the local stand-in, this is the only file that changes.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from agent.marketplace_port import (
    AuthError,
    ClientHistory,
    Conflict,
    ConnectsBalance,
    InsufficientConnects,
    InvalidRequest,
    Job,
    JobSummary,
    MarketplaceError,
    Message,
    NotFound,
    PlatformPolicy,
    PolicyRejected,
    Proposal,
    ProposalIn,
    RateLimited,
    SubmissionPreview,
    SubmissionReceipt,
    Unavailable,
)


class GigSimAdapter:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.25,
        max_retry_wait: float = 10.0,
        max_requests_per_minute: int | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        self.max_retries, self.backoff_base, self.max_retry_wait = max_retries, backoff_base, max_retry_wait
        self._limit, self._sleep, self._clock = max_requests_per_minute, sleep, clock
        self._window: deque[float] = deque()
        self._rng = random.Random(0)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- transport ------------------------------------------------------------------------------------------
    async def _throttle(self) -> None:
        if not self._limit:
            return
        now, w = self._clock(), self._window
        while w and now - w[0] >= 60:
            w.popleft()
        if len(w) >= self._limit:
            await self._sleep(60 - (now - w[0]) + 0.05)
            w.popleft()
        w.append(self._clock())

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        wait = retry_after if retry_after is not None else self.backoff_base * 2**attempt * (1 + self._rng.random() * 0.25)
        return min(wait, self.max_retry_wait)

    async def _request(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                       idempotency_key: str | None = None) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        for attempt in range(self.max_retries + 1):
            await self._throttle()
            try:
                r = await self._http.request(method, path, json=json, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise Unavailable(f"GigSim unreachable: {exc}") from exc
                await self._sleep(self._backoff(attempt))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = _retry_after(r)
                if attempt == self.max_retries:
                    if r.status_code == 429:
                        raise RateLimited("GigSim rate limit exceeded", retry_after or 1.0)
                    raise Unavailable(f"GigSim error {r.status_code}")
                await self._sleep(self._backoff(attempt, retry_after))
                continue
            if r.status_code >= 400:
                raise _map_error(r)
            return r.json()
        raise Unavailable("retries exhausted")  # pragma: no cover

    # ---- reads -------------------------------------------------------------------------------------------------
    async def search_jobs(self, query: str, limit: int = 20, offset: int = 0) -> list[JobSummary]:
        data = await self._request("GET", "/v1/jobs", params={"q": query, "limit": limit, "offset": offset})
        return [JobSummary.model_validate(x) for x in data["items"]]

    async def get_job(self, job_id: str) -> Job:
        return Job.model_validate(await self._request("GET", f"/v1/jobs/{job_id}"))

    async def get_client_history(self, client_id: str) -> ClientHistory:
        return ClientHistory.model_validate(await self._request("GET", f"/v1/clients/{client_id}/history"))

    async def get_connects_balance(self) -> ConnectsBalance:
        return ConnectsBalance.model_validate(await self._request("GET", "/v1/connects/balance"))

    async def get_platform_policy(self) -> PlatformPolicy:
        return PlatformPolicy.model_validate(await self._request("GET", "/v1/platform/policy"))

    async def get_proposal(self, proposal_id: str) -> Proposal:
        return Proposal.model_validate(await self._request("GET", f"/v1/proposals/{proposal_id}"))

    async def list_messages(self, proposal_id: str) -> list[Message]:
        data = await self._request("GET", "/v1/messages", params={"proposal_id": proposal_id})
        return [Message.model_validate(x) for x in data["items"]]

    # ---- writes ------------------------------------------------------------------------------------------------
    async def create_proposal_draft(self, draft: ProposalIn, idempotency_key: str) -> Proposal:
        data = await self._request("POST", "/v1/proposals", json=draft.model_dump(), idempotency_key=idempotency_key)
        return Proposal.model_validate(data)

    async def preview_submission(self, proposal_id: str) -> SubmissionPreview:
        data = await self._request("POST", f"/v1/proposals/{proposal_id}/submit", params={"dry_run": "true"})
        return SubmissionPreview.model_validate(data)

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> SubmissionReceipt:
        data = await self._request("POST", f"/v1/proposals/{proposal_id}/submit", idempotency_key=idempotency_key)
        return SubmissionReceipt.model_validate(data)

    async def send_message(self, proposal_id: str, text: str, idempotency_key: str) -> Message:
        data = await self._request("POST", "/v1/messages", json={"proposal_id": proposal_id, "text": text},
                                   idempotency_key=idempotency_key)
        return Message.model_validate(data)


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _map_error(r: httpx.Response) -> MarketplaceError:
    try:
        detail = r.json().get("detail", {})
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):  # FastAPI validation errors are a list
        detail = {"code": "invalid_request", "message": str(detail)[:300]}
    msg, code = detail.get("message", f"HTTP {r.status_code}"), detail.get("code", "")
    if r.status_code in (401, 403):
        return AuthError(msg)
    if r.status_code == 404:
        return NotFound(msg)
    if r.status_code == 402 or code == "insufficient_connects":
        return InsufficientConnects(msg)
    if code == "policy_violation":
        return PolicyRejected(msg, detail.get("rule"))
    if r.status_code == 409:
        return Conflict(msg)
    return InvalidRequest(msg)
