# One command for the whole local stack: GigSim + 3 MCP servers + API (:8000) + web UI (:3000), each in its own window.
#   .\scripts\dev_all.ps1            then open http://localhost:3000   (close the windows to stop)
# Adds MCP_BEARER_TOKEN=devtoken to .env if it is missing (local development only).
param([string]$Dataset = "sample")
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
if (-not (Select-String -Path $envFile -Pattern '^\s*MCP_BEARER_TOKEN\s*=\s*\S' -Quiet)) {
    Add-Content -Path $envFile -Value "`nMCP_BEARER_TOKEN=devtoken"
    Write-Host "Added MCP_BEARER_TOKEN=devtoken to .env"
}
& (Join-Path $PSScriptRoot "dev_stack.ps1") -Dataset $Dataset
Start-Sleep -Seconds 5
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; uv run uvicorn apps.api.main:app --port 8000"
Start-Sleep -Seconds 3
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\apps\web'; npm run dev"
Write-Host "API http://localhost:8000/health/ready | UI http://localhost:3000 (first page load compiles, give it ~10 s)"
