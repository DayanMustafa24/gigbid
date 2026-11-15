"""Schema snapshot (assignment A7): fails when any MCP tool signature (name, description, input/output schema, annotations) changes.

First run writes tests/contract/snapshots/tool_schemas.json and skips: commit that file. After that any change fails the test.
An intentional change: `$env:UPDATE_SNAPSHOTS="1"; uv run pytest tests/contract/test_tool_schemas.py`, review the diff, commit.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SNAPSHOT = Path(__file__).parent / "snapshots" / "tool_schemas.json"


async def _tools(server) -> dict:
    memory = pytest.importorskip("mcp.shared.memory")
    async with memory.create_connected_server_and_client_session(server._mcp_server) as client:
        tools = (await client.list_tools()).tools
    return {t.name: {"description": t.description, "input": t.inputSchema, "output": t.outputSchema,
                     "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None}
            for t in sorted(tools, key=lambda t: t.name)}


async def current(tmp_path) -> dict:
    from mcp_servers.marketplace.server import create_server as marketplace
    from mcp_servers.portfolio.server import create_server as portfolio
    from mcp_servers.portfolio.store import PortfolioStore
    from mcp_servers.workspace.server import create_server as workspace
    from mcp_servers.workspace.store import WorkspaceConfig, WorkspaceStore

    return {
        "portfolio": await _tools(portfolio(PortfolioStore(tmp_path))),
        "marketplace": await _tools(marketplace(SimpleNamespace(), audit_path=tmp_path / "m.jsonl", secret="s" * 24)),
        "workspace": await _tools(workspace(WorkspaceStore(WorkspaceConfig(), tmp_path / "w.json"), audit_path=tmp_path / "w.jsonl")),
    }


async def test_tool_signatures_match_committed_snapshot(tmp_path):
    now = json.loads(json.dumps(await current(tmp_path), sort_keys=True))
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"snapshot written to {SNAPSHOT}: review and commit it")
    assert now == json.loads(SNAPSHOT.read_text(encoding="utf-8")), (
        "an MCP tool signature changed. If intentional: UPDATE_SNAPSHOTS=1 pytest tests/contract/test_tool_schemas.py, review, commit")
