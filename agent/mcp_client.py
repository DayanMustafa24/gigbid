"""Agent-side MCP gateway (3.4): the ONLY way graph nodes reach tools.

Responsibilities: per-node allowlists (least privilege, from config), timeouts, bounded retries with backoff,
Retry-After, a circuit breaker that turns a dead server into fast `McpUnavailable` (degraded mode: the API shows a
banner and queues work) and recovers automatically when the server answers again, trace-id + caller propagation.

Transports are pluggable: HttpTransport (production: Streamable HTTP through langchain-mcp-adapters' client) and
InMemoryTransport (tests, single-process demo, CI). The policy above is transport-independent and unit-tested.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from agent.observability.context import TRACE_HEADER, get_trace_id
from agent.settings import load_config

_ERR = re.compile(
    r"^(?:Error executing tool \w+: )?(?P<code>\w+): (?P<msg>.*?) "
    r"\(retryable=(?P<retry>true|false)(?: retry_after=(?P<after>[\d.]+))?(?: rule=(?P<rule>[\w.-]+))?"
)


class McpError(Exception):
    pass


class McpNotAllowed(McpError):
    """Node asked for a tool outside its allowlist. Never retried."""


class McpToolError(McpError):
    """The server ran the tool and reported an error (stable `code`, `retryable` flag)."""

    def __init__(self, code: str, message: str, retryable: bool = False, retry_after: float | None = None, rule: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.retryable, self.retry_after, self.rule = code, retryable, retry_after, rule


class McpUnavailable(McpError):
    """Server unreachable / timed out / circuit open. Callers degrade: queue the work, show a banner."""


def parse_tool_error(text: str) -> McpToolError:
    m = _ERR.match(text.strip())
    if not m:
        return McpToolError("ToolError", text.strip()[:300], retryable=False)
    return McpToolError(m["code"], m["msg"], m["retry"] == "true", float(m["after"]) if m["after"] else None, m["rule"])


class Transport(Protocol):
    async def call(self, server: str, tool: str, args: dict[str, Any], *, caller: str) -> Any: ...


def _unwrap(res) -> Any:
    if res.isError:
        raise parse_tool_error(" ".join(getattr(c, "text", "") for c in res.content))
    sc = res.structuredContent
    return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc


class InMemoryTransport:
    """Connects to FastMCP server objects in-process: {name: FastMCP}."""

    def __init__(self, servers: dict[str, Any]):
        self.servers = servers

    async def call(self, server: str, tool: str, args: dict[str, Any], *, caller: str = "agent") -> Any:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(self.servers[server]._mcp_server) as client:
            res = await client.call_tool(tool, args)
        # unwrap OUTSIDE the session: an error raised inside it is wrapped in an ExceptionGroup
        return _unwrap(res)


class HttpTransport:
    """Streamable HTTP via langchain-mcp-adapters' MultiServerMCPClient (one short session per call)."""

    def __init__(self, urls: dict[str, str], bearer: str | None):
        self.urls, self.bearer = urls, bearer

    async def call(self, server: str, tool: str, args: dict[str, Any], *, caller: str = "agent") -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        headers = {TRACE_HEADER: get_trace_id(), "x-gigbid-user": caller}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        conn = {"transport": "streamable_http", "url": self.urls[server], "headers": headers}
        client = MultiServerMCPClient({server: conn})
        async with client.session(server) as session:
            res = await session.call_tool(tool, args)
        return _unwrap(res)


class McpGateway:
    def __init__(
        self,
        transport: Transport,
        allowlists: dict[str, dict[str, list[str]]],
        *,
        timeout_s: float = 15,
        max_retries: int = 2,
        backoff_base_s: float = 0.25,
        max_retry_wait_s: float = 10,
        cooldown_s: float = 30,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.transport, self.allow = transport, allowlists
        self.timeout_s, self.max_retries, self.backoff = timeout_s, max_retries, backoff_base_s
        self.max_retry_wait, self.cooldown_s, self._sleep, self._clock = max_retry_wait_s, cooldown_s, sleep, clock
        self._down_until: dict[str, float] = {}
        self._last_error: dict[str, str] = {}

    # ---- construction from config -------------------------------------------------------------------------------
    @classmethod
    def from_config(cls, transport: Transport | None = None, cfg: dict | None = None) -> McpGateway:
        cfg = (cfg or load_config())["mcp"]
        if transport is None:
            urls = {n: os.environ.get(s["url_env"], s["default_url"]) for n, s in cfg["servers"].items()}
            transport = HttpTransport(urls, os.environ.get("MCP_BEARER_TOKEN"))
        return cls(
            transport,
            cfg["allowlists"],
            timeout_s=cfg["timeout_s"],
            max_retries=cfg["max_retries"],
            backoff_base_s=cfg["backoff_base_s"],
            max_retry_wait_s=cfg["max_retry_wait_s"],
            cooldown_s=cfg["cooldown_s"],
        )

    # ---- introspection (API/UI banner) ---------------------------------------------------------------------------
    def allowed_tools(self, node: str, server: str) -> list[str]:
        return list(self.allow.get(node, {}).get(server, []))

    def health(self) -> dict[str, dict[str, Any]]:
        now = self._clock()
        servers = {s for per in self.allow.values() for s in per} | set(self._down_until)
        return {
            s: {"up": self._down_until.get(s, 0) <= now, "last_error": self._last_error.get(s)}
            for s in sorted(servers)
        }

    # ---- the call --------------------------------------------------------------------------------------------------
    async def call(self, node: str, server: str, tool: str, args: dict[str, Any] | None = None) -> Any:
        if tool not in self.allow.get(node, {}).get(server, []):
            raise McpNotAllowed(f"node {node!r} may not call {server}.{tool}")
        if self._down_until.get(server, 0) > self._clock():
            raise McpUnavailable(f"{server} is down: {self._last_error.get(server, 'unknown')} (circuit open)")
        args = args or {}
        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.wait_for(self.transport.call(server, tool, args, caller=node), self.timeout_s)
                self._down_until.pop(server, None)
                self._last_error.pop(server, None)
                return result
            except McpToolError as e:
                if not e.retryable or attempt == self.max_retries:
                    raise
                wait = e.retry_after if e.retry_after is not None else self._backoff(attempt)
                await self._sleep(min(wait, self.max_retry_wait))
            except (McpNotAllowed, McpUnavailable):
                raise
            except Exception as e:                      # timeout, connection refused, protocol error
                self._last_error[server] = f"{type(e).__name__}: {e}"[:200]
                if attempt == self.max_retries:
                    self._down_until[server] = self._clock() + self.cooldown_s
                    raise McpUnavailable(
                        f"{server}.{tool} failed after {attempt + 1} attempts: {self._last_error[server]}"
                    ) from e
                await self._sleep(min(self._backoff(attempt), self.max_retry_wait))
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return self.backoff * (2**attempt)
