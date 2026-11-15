# GigBid Portfolio MCP server

Read-only MCP server that lets any MCP client (Claude Desktop, Cursor, MCP Inspector) search a freelancer's portfolio evidence.
Tools: `search_evidence`, `get_case_study`, `list_skills`, `get_profile` (all `readOnly`). Resources: `portfolio://case/{id}`, `portfolio://proposal/{id}`, `portfolio://profile`.
Prompts: `ground_claim`, `summarize_fit`. Gold labels and fabrication labels are never exposed (leak test in `tests/unit/test_portfolio_server.py`).

## Run (from a clone of the repository)
```
uv sync
uv run python -m mcp_servers.portfolio.server            # Streamable HTTP on http://localhost:9101/mcp
MCP_TRANSPORT=stdio uv run python -m mcp_servers.portfolio.server   # stdio, for Claude Desktop
```
## Run with Docker (no Python needed)
```
docker build -f infra/docker/python.Dockerfile -t gigbid-python .
docker run --rm -p 9101:9101 -e MCP_HOST=0.0.0.0 gigbid-python python -m mcp_servers.portfolio.server
```
## Claude Desktop (stdio)
```json
{"mcpServers": {"gigbid-portfolio": {"command": "uv", "args": ["run", "--directory", "/path/to/gigbid", "python", "-m", "mcp_servers.portfolio.server"],
  "env": {"MCP_TRANSPORT": "stdio"}}}}
```
Data: the synthetic personas in `data/synthetic/personas` (override the directory to serve your own portfolio, see `apps/api/portfolio.py` for the layout).
Inspect with: `npx @modelcontextprotocol/inspector` and connect to the URL above.
