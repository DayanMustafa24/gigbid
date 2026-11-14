"""One trace id flows UI -> API -> graph -> MCP servers -> audit log (E3). Logging always masks PII."""
from __future__ import annotations

import contextvars
import logging
import uuid

from agent.security.pii import mask

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
TRACE_HEADER = "x-gigbid-trace-id"


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(value: str | None = None) -> str:
    tid = value or new_trace_id()
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


class MaskingTraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(record.getMessage())
        record.args = None
        record.trace_id = get_trace_id()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"))
    handler.addFilter(MaskingTraceFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
