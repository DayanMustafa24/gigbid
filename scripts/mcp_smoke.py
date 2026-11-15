"""Live smoke test of the three MCP servers over real Streamable HTTP, through the agent gateway.

Start the servers first (3 terminals, see docs/RUNBOOK.md or the commands printed by --help), then:
    uv run python -m scripts.mcp_smoke
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

from agent.mcp_client import McpGateway, McpToolError, McpUnavailable
from agent.settings import ROOT

CHECKS = [
    ("portfolio", "fit_matcher", "get_profile", {"freelancer_id": "ai_ml"}),
    (
        "portfolio", "fit_matcher", "search_evidence",
        {"requirement": "RAG chatbot with citations", "k": 3, "freelancer_id": "ai_ml"},
    ),
    ("marketplace", "scout", "search_jobs", {"query": "python", "limit": 3}),
    ("marketplace", "compliance", "get_platform_policy", {}),
    ("workspace", "client_liaison", "get_free_slots", {"date_from": (date.today() + timedelta(days=1)).isoformat()}),
]


async def main() -> int:
    load_dotenv(ROOT / ".env")
    gw = McpGateway.from_config()
    bad = 0
    for server, node, tool, args in CHECKS:
        try:
            out = await gw.call(node, server, tool, args)
            size = len(out) if isinstance(out, list) else len(out.get("results", out)) if isinstance(out, dict) else 1
            print(f"OK    {server}.{tool}  ({size} item(s))")
        except (McpUnavailable, McpToolError) as e:
            bad += 1
            print(f"FAIL  {server}.{tool}: {e}")
    print("health:", gw.health())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
