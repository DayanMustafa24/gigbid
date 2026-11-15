"""Untrusted-text handling for prompts (assignment R9, layer one of the injection defence).

Job posts and client messages are DATA. `quote_untrusted` wraps them in a tag and removes anything that could close the tag
or forge another one, so the text cannot break out of its quotation. Detection/classification (layers two and three) is Phase 5.8.
"""
from __future__ import annotations

import re

_TAGLIKE = re.compile(r"</?\s*(?:job_post|client_message|evidence|claim|system|assistant|user|instructions?)\b[^>]*>", re.I)


def neutralise(text: str, limit: int = 6000) -> str:
    """Drop tag-like fragments that mimic our delimiters or chat roles; cap length. Content is otherwise untouched."""
    return _TAGLIKE.sub("[tag removed]", text or "")[:limit]


def quote_untrusted(tag: str, text: str, limit: int = 6000) -> str:
    return f"<{tag}>\n{neutralise(text, limit)}\n</{tag}>"
