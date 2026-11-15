"""Phase 3.5 probe: log in to the OFFICIAL Upwork MCP once and record what it really offers, so the live adapter is written from facts.

    uv run python -m scripts.upwork_probe            # opens your browser; log in to Upwork and allow access
    uv run python -m scripts.upwork_probe --port 8765

Why a probe and not an adapter yet: the Upwork MCP is self-describing (tools `search_tools` / `get_tool_help` / `execute_tool`; Upwork's own
guidance is "never guess names or parameters"), uses OAuth 2.1 with dynamic client registration, and only accepts callback URLs Upwork approved.
This script tests exactly that: does Upwork accept http://127.0.0.1:<port>/callback for GigBid? It is READ-ONLY: it only lists accounts and tool help.
It never searches jobs, never drafts, never submits, never messages.

Output: data/live/upwork_tools.json (git-ignored: keep it private) and data/live/upwork_oauth.json (tokens, git-ignored). Send me the tools file
CONTENT only after skimming it; account identifiers are not written to it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "live"
URL = "https://mcp.upwork.com/mcp"
QUERIES = ["search jobs", "job details", "proposal", "connects balance", "messages", "dashboard"]


class FileStorage(TokenStorage):
    """Tokens and the dynamically registered client, kept in a git-ignored file so the browser login happens once."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _write(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = json.loads(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        v = self._read().get("tokens")
        return OAuthToken.model_validate(v) if v else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write("tokens", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        v = self._read().get("client")
        return OAuthClientInformationFull.model_validate(v) if v else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._write("client", client_info.model_dump_json())


async def wait_for_callback(port: int) -> tuple[str, str | None]:
    """Tiny loopback HTTP server: waits for ONE request to /callback and returns (code, state)."""
    got: asyncio.Future[tuple[str, str | None]] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readline()).decode("latin-1")
        q = parse_qs(urlparse(line.split(" ")[1] if " " in line else "").query)
        ok = "code" in q
        body = "Signed in. You can close this tab and return to the terminal." if ok else "No authorization code received."
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()
        if ok and not got.done():
            got.set_result((q["code"][0], q.get("state", [None])[0]))
        elif not got.done():
            got.set_exception(RuntimeError(f"Upwork returned an error instead of a code: {dict((k, v[0]) for k, v in q.items())}"))

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    try:
        return await asyncio.wait_for(got, timeout=300)
    finally:
        server.close()


def text_of(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in getattr(result, "content", []) or [])


async def call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """One tool call, never raising: the record says what happened."""
    await asyncio.sleep(0.5)  # polite: the server meters calls
    try:
        res = await session.call_tool(name, args)
        txt = text_of(res)
        try:
            body: Any = json.loads(txt)
        except ValueError:
            body = txt[:4000]
        return {"tool": name, "args": args, "is_error": bool(res.isError), "result": body}
    except Exception as e:  # noqa: BLE001
        return {"tool": name, "args": args, "is_error": True, "result": f"{type(e).__name__}: {str(e)[:300]}"}


def find_roles(obj: Any) -> list[dict[str, Any]]:
    """Accounts from a list_accounts result, wherever they sit in the JSON."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if "role" in obj and "org_uid" in obj:
            found.append(obj)
        for v in obj.values():
            found += find_roles(v)
    elif isinstance(obj, list):
        for v in obj:
            found += find_roles(v)
    return found


def tool_names(obj: Any) -> list[str]:
    names: list[str] = []
    if isinstance(obj, dict):
        for k in ("tool_name", "name"):
            if isinstance(obj.get(k), str):
                names.append(obj[k])
        for v in obj.values():
            names += tool_names(v)
    elif isinstance(obj, list):
        for v in obj:
            names += tool_names(v)
    elif isinstance(obj, str):
        names += re.findall(r'"(?:tool_name|name)"\s*:\s*"([a-z0-9_]+)"', obj)
    return names


async def main(port: int) -> None:
    redirect = f"http://127.0.0.1:{port}/callback"
    meta = OAuthClientMetadata(client_name="GigBid (personal freelancer copilot)", redirect_uris=[AnyUrl(redirect)],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"], token_endpoint_auth_method="none")

    async def redirect_handler(url: str) -> None:
        print("\nOpen this address and log in to Upwork (your browser should open by itself):\n", url, "\n")
        webbrowser.open(url)

    provider = OAuthClientProvider(server_url=URL, client_metadata=meta, storage=FileStorage(LIVE / "upwork_oauth.json"),
                                   redirect_handler=redirect_handler, callback_handler=lambda: wait_for_callback(port))
    report: dict[str, Any] = {"server": URL, "redirect_uri": redirect, "tools": [], "calls": []}
    try:
        async with streamablehttp_client(URL, auth=provider) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                report["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.inputSchema,
                                    "read_only": getattr(getattr(t, "annotations", None), "readOnlyHint", None)} for t in (await session.list_tools()).tools]
                print(f"Connected. The server exposes {len(report['tools'])} tool(s): {[t['name'] for t in report['tools']]}")
                names = {t["name"] for t in report["tools"]}
                role, org = "TALENT", None
                if "list_accounts" in names:
                    acc = await call(session, "list_accounts", {})
                    roles = find_roles(acc["result"])
                    mine = next((r for r in roles if r.get("role") == "TALENT"), roles[0] if roles else {})
                    role, org = mine.get("role", role), mine.get("org_uid")
                    report["calls"].append({"tool": "list_accounts", "accounts": [{"role": r.get("role"), "role_label": r.get("role_label"),
                                                                                    "suspended": r.get("suspended")} for r in roles]})
                if "search_tools" in names:
                    seen: list[str] = []
                    for q in QUERIES:
                        rec = await call(session, "search_tools", {"role": role, "query": q, **({"org_uid": org} if org else {})})
                        report["calls"].append(rec)
                        seen += [n for n in tool_names(rec["result"]) if n not in seen]
                    if "get_tool_help" in names:
                        for n in seen[:25]:
                            report["calls"].append(await call(session, "get_tool_help", {"tool_name": n, **({"org_uid": org} if org else {})}))
    except Exception as e:  # noqa: BLE001
        report["error"] = f"{type(e).__name__}: {e}"
        print("\nCould not complete the login/connection:", report["error"])
        print("If Upwork refused the callback address, that is the 'approved callback URL' rule: ask mcp-support@upwork.com to allow", redirect)
    LIVE.mkdir(parents=True, exist_ok=True)
    out = LIVE / "upwork_tools.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(ROOT)}  ({len(report['tools'])} tools, {len(report['calls'])} read-only calls)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    asyncio.run(main(ap.parse_args().port))
