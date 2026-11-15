"""Human-approval tokens (R4). Issued by the API ONLY when a human approves; verified by the Marketplace MCP server.

A token is bound to (action, subject, expiry), so an approval for proposal A cannot submit proposal B, an approved
message text cannot be swapped for another, and a stale approval expires. HMAC-SHA256 with APP_SECRET_KEY.
The graph never mints tokens itself: write tools are unreachable without one produced by the interrupt resume.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

from agent.settings import get_settings

DEFAULT_TTL_S = 900


class ApprovalError(Exception):
    """Token missing, forged, expired, or issued for something else."""


@dataclass(frozen=True)
class ApprovalClaims:
    action: str
    subject: str
    expires_at: int
    approver: str


def message_subject(proposal_id: str, text: str) -> str:
    """Subject for send_message: ties the approval to the exact text the human saw."""
    return f"{proposal_id}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue(action: str, subject: str, *, approver: str = "human", ttl: int = DEFAULT_TTL_S,
          secret: str | None = None, now: float | None = None) -> str:
    secret = secret if secret is not None else get_settings().app_secret_key
    exp = int((now if now is not None else time.time()) + ttl)
    payload = f"{action}|{subject}|{exp}|{approver}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + _sig(secret, payload)


def verify(token: str | None, action: str, subject: str, *, secret: str | None = None,
           now: float | None = None) -> ApprovalClaims:
    if not token or "." not in token:
        raise ApprovalError("human approval token required")
    secret = secret if secret is not None else get_settings().app_secret_key
    body, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        act, subj, exp, approver = payload.split("|", 3)
        exp_i = int(exp)
    except Exception as e:  # malformed
        raise ApprovalError("malformed approval token") from e
    if not hmac.compare_digest(sig, _sig(secret, payload)):
        raise ApprovalError("invalid approval token signature")
    if act != action or subj != subject:
        raise ApprovalError(f"approval token was issued for {act!r} on a different subject")
    if (now if now is not None else time.time()) > exp_i:
        raise ApprovalError("approval token expired")
    return ApprovalClaims(act, subj, exp_i, approver)


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ApprovalGrant:
    """The authority to mint tokens for ONE run. Created by the API at the moment a human decides (approve/edit) and handed to the
    deterministic executor/sender through `Deps.approval`; before that decision no grant exists, so there is nothing to mint with.
    `approved_sha` is set when the human approved the draft as shown: a token is then refused for any other text."""

    approver: str
    run_id: str
    approved_sha: str | None = None
    ttl: int = DEFAULT_TTL_S
    secret: str | None = None

    def mint(self, action: str, subject: str, *, text: str | None = None) -> str:
        if self.approved_sha is not None and text is not None and text_sha(text) != self.approved_sha:
            raise ApprovalError("the text differs from what the human approved")
        return issue(action, subject, approver=self.approver, ttl=self.ttl, secret=self.secret)
