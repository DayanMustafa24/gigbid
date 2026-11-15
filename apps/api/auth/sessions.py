"""Authentication: scrypt password hashes (stdlib), HS256 session tokens (PyJWT) in an httpOnly cookie or a Bearer header, a small
login throttle. Tenant scoping starts here: `current_user` is the only source of `owner_id` for every query.

CSRF: the session cookie is SameSite=Lax and every state-changing endpoint takes a JSON body, which a cross-site form cannot send
without a CORS preflight (we allow no foreign origins). Passwords are never logged; tokens are never returned in the body.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

import jwt

COOKIE = "gigbid_session"
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def make_token(user_id: str, role: str, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": user_id, "role": role, "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}, secret, algorithm="HS256")


def read_token(token: str, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def check_secret(secret: str, env: str) -> None:
    """A deployment must not sign sessions with a placeholder."""
    if env == "production" and (secret in ("", "replace_me", "dev-only-change-me") or len(secret) < 24):
        raise RuntimeError("APP_SECRET_KEY must be a random value of at least 24 characters when APP_ENV=production")


class LoginThrottle:
    """Sliding window: at most `limit` failed logins per key (ip+email) per `window_s`. In-process; Redis-backed in multi-instance deployments."""

    def __init__(self, limit: int = 5, window_s: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q, now = self._fails[key], self.clock()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.limit

    def fail(self, key: str) -> None:
        self._prune(key).append(self.clock())

    def success(self, key: str) -> None:
        self._fails.pop(key, None)
