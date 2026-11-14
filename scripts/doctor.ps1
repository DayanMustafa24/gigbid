# Environment check + installer for Windows (PowerShell). Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1            # check only
#   powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Install   # install missing (winget)
param([switch]$Install)

$tools = @(
  @{ name = "python"; cmd = "python --version";        winget = "Python.Python.3.12" },
  @{ name = "uv";     cmd = "uv --version";            winget = "astral-sh.uv" },
  @{ name = "node";   cmd = "node --version";          winget = "OpenJS.NodeJS.LTS" },
  @{ name = "git";    cmd = "git --version";           winget = "Git.Git" },
  @{ name = "docker"; cmd = "docker --version";        winget = "Docker.DockerDesktop" },
  @{ name = "compose";cmd = "docker compose version";  winget = $null },
  @{ name = "ollama"; cmd = "ollama --version";        winget = "Ollama.Ollama" }
)

foreach ($t in $tools) {
  try {
    $out = Invoke-Expression $t.cmd 2>&1 | Select-Object -First 1
    Write-Host ("[ok]      {0,-8} {1}" -f $t.name, $out)
  } catch {
    Write-Host ("[MISSING] {0}" -f $t.name) -ForegroundColor Yellow
    if ($Install -and $t.winget) { winget install -e --id $t.winget }
  }
}

try { nvidia-smi --query-gpu=name,memory.total --format=csv,noheader } catch { Write-Host "[info] no NVIDIA GPU detected (CPU/hosted models will be used)" }
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Write-Host "[info] RAM: $ram GB"
try { wsl --status | Select-Object -First 2 } catch { Write-Host "[MISSING] WSL2 (needed by Docker Desktop): run 'wsl --install' then reboot" -ForegroundColor Yellow }
