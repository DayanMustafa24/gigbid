"""PII / secret masking for logs and traces (R9, E4)."""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}"), "Bearer [token]"),
    (re.compile(r"\b(?:sk|gsk|ghp|github_pat|xox[bp])[-_][A-Za-z0-9_-]{16,}"), "[api-key]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[iban]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d"), "[phone]"),
    (re.compile(r"(?i)\b(?:paypal|venmo|zelle|cashapp)\b[:\s]*\S+"), "[payment-handle]"),
]

_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "authorization")


def mask(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def mask_obj(obj):
    """Recursively mask strings in dicts/lists (for trace attributes and audit logs)."""
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: ("[secret]" if _is_secret_key(k) else mask_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(mask_obj(v) for v in obj)
    return obj


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and any(s in key.lower() for s in _SECRET_KEY_PARTS)
