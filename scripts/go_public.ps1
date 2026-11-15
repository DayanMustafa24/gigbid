# Put GigBid on the internet FROM THIS PC: no credit card, no cloud account. Free Cloudflare "quick tunnel" gives a public https address.
#   powershell -ExecutionPolicy Bypass -File scripts\go_public.ps1            (first run: ~5-10 min, builds the web app)
#   powershell -ExecutionPolicy Bypass -File scripts\go_public.ps1 -SkipBuild (later runs: ~1 min)
# It: fills missing secrets in .env (backup: .env.bak), starts GigSim + 3 MCP servers + API + web, opens the tunnel, prints the reviewer login.
# The public address works only while this PC is on, awake and online, and changes every time the tunnel restarts.
#   Stable address (free ngrok account, no card): add  -NgrokDomain your-name.ngrok-free.app  (see the steps given in chat)
param([string]$Dataset = "stream", [switch]$SkipBuild, [string]$NgrokDomain = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root "apps\web"
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
Copy-Item $envFile (Join-Path $root ".env.bak") -Force

function Get-Rand([int]$n) { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ }) }
function Get-EnvValue([string]$key) {
    $m = Select-String -Path $envFile -Pattern "^\s*$key\s*=\s*(.*)$" | Select-Object -Last 1
    if ($m) { ($m.Matches[0].Groups[1].Value -replace '\s+#.*$', '').Trim() } else { "" }
}
function Set-EnvValue([string]$key, [string]$value) {
    $lines = @(Get-Content $envFile); $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*$key\s*=") { $lines[$i] = "$key=$value"; $found = $true } }
    if (-not $found) { $lines += "$key=$value" }
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
}

Write-Host "1/5 Checking secrets in .env ..."
$sec = Get-EnvValue "APP_SECRET_KEY";   if ($sec.Length -lt 24 -or $sec -eq "replace_me") { Set-EnvValue "APP_SECRET_KEY" (Get-Rand 48) }
$tok = Get-EnvValue "MCP_BEARER_TOKEN"; if ($tok -in @("", "replace_me", "devtoken")) { Set-EnvValue "MCP_BEARER_TOKEN" (Get-Rand 32) }
if ((Get-EnvValue "TOKEN_ENCRYPTION_KEY") -eq "") {
    Push-Location $root
    $fk = (uv run python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())").Trim()
    Pop-Location
    Set-EnvValue "TOKEN_ENCRYPTION_KEY" $fk
}
if ((Get-EnvValue "REVIEWER_EMAIL") -eq "") { Set-EnvValue "REVIEWER_EMAIL" "reviewer@gigbid.demo" }
if ((Get-EnvValue "REVIEWER_PASSWORD") -eq "") { Set-EnvValue "REVIEWER_PASSWORD" (Get-Rand 16) }
$reviewerEmail = Get-EnvValue "REVIEWER_EMAIL"; $reviewerPass = Get-EnvValue "REVIEWER_PASSWORD"

Write-Host "2/5 Starting GigSim + MCP servers ..."
& (Join-Path $PSScriptRoot "dev_stack.ps1") -Dataset $Dataset
Start-Sleep -Seconds 6

Write-Host "3/5 Starting the API ..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; `$env:APP_ENV='production'; `$env:COOKIE_SECURE='true'; `$env:CORS_ORIGINS=''; uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers"

Write-Host "4/5 Web app ..."
Push-Location $web
if (-not $SkipBuild) {
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
}
if (-not (Test-Path ".next\standalone\server.js")) { throw "No production build found: run again without -SkipBuild" }
$dest = ".next\standalone\.next\static"
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
Copy-Item ".next\static" $dest -Recurse -Force
Pop-Location
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$web\.next\standalone'; `$env:PORT='3000'; `$env:HOSTNAME='127.0.0.1'; node server.js"

Write-Host "Waiting for the API and the web app ..."
$ok = $false
foreach ($i in 1..40) {
    try {
        Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3 | Out-Null
        Invoke-WebRequest "http://127.0.0.1:3000/login" -UseBasicParsing -TimeoutSec 3 | Out-Null
        $ok = $true; break
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $ok) { Write-Host "API or web did not come up in 2 minutes: read the error in their windows and paste it to me." -ForegroundColor Red; exit 1 }

Write-Host "5/5 Public tunnel ..."
if ($NgrokDomain) {
    if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
        winget install --id Ngrok.Ngrok -e --accept-source-agreements --accept-package-agreements
        Write-Host "ngrok installed. CLOSE this window, open a NEW PowerShell, run:  ngrok config add-authtoken <your token>  then run this script again with -SkipBuild -NgrokDomain $NgrokDomain" -ForegroundColor Yellow
        exit 0
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "ngrok http --url=$NgrokDomain 3000"
    Write-Host "Public (stable): https://$NgrokDomain  (first visit shows an ngrok notice page: click Visit Site)" -ForegroundColor Green
}
else {
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
        Write-Host "cloudflared installed. CLOSE this window, open a NEW PowerShell, and run this script again with -SkipBuild." -ForegroundColor Yellow
        exit 0
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cloudflared tunnel --url http://127.0.0.1:3000"
}

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Green
Write-Host " Local:  http://localhost:3000"
Write-Host " Public: look in the NEW 'cloudflared' window for a line like  https://something-random.trycloudflare.com"
Write-Host " Reviewer login (put this in the submission's private comment):"
Write-Host "   email:    $reviewerEmail"
Write-Host "   password: $reviewerPass"
Write-Host " First login asks you to pick a persona (onboarding): do it once, then the sidebar works."
Write-Host " Keep this PC on and awake. To stop everything: close all the PowerShell windows."
Write-Host "=============================================================" -ForegroundColor Green
