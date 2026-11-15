# Starts the local marketplace stack in separate windows: GigSim (:8081) + Portfolio/Marketplace/Workspace MCP servers (:9101-9103).
# Then run the API yourself:  uv run uvicorn apps.api.main:app --port 8000   -> /health/ready should show marketplace "up".
# Needs MCP_BEARER_TOKEN in .env (e.g. MCP_BEARER_TOKEN=devtoken). Close the windows to stop.
param([string]$Dataset = "sample")
$root = Split-Path -Parent $PSScriptRoot
$cmds = @(
    "`$env:GIGSIM_DATASET='$Dataset'; `$env:GIGSIM_FAULT_RATE='0'; uv run python -m gigsim_local",
    "uv run python -m mcp_servers.portfolio.server",
    "uv run python -m mcp_servers.marketplace.server",
    "uv run python -m mcp_servers.workspace.server"
)
foreach ($c in $cmds) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $c"
    Start-Sleep -Milliseconds 800
}
Write-Host "Started 4 windows. Wait ~5 s, then start the API."
