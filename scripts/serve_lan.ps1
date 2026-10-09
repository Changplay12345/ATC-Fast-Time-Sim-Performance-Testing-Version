<#
  Serve the web version from this PC to other computers on the same network
  ("self-hosted" deployment, in its simplest form).

      powershell -ExecutionPolicy Bypass -File scripts\serve_lan.ps1
      powershell -ExecutionPolicy Bypass -File scripts\serve_lan.ps1 -SkipBuild   # front end already built for this address

  What it does:
    1. finds this PC's network address (or takes -Ip)
    2. allows ports 3000 and 8000 in through Windows Firewall, for this
       network only (asks for administrator approval once)
    3. builds the front end with the engine's address baked in
       (NEXT_PUBLIC_API_BASE is read at build time, so a new address needs
       a new build)
    4. starts the engine (uvicorn, all interfaces, hosted mode) and the front
       end (next start) in two console windows
    5. waits until both answer and prints the address to open on the
       other computer

  Stop it by closing the two console windows. Flight plans sent from the
  other computer are computed here; nothing is written to disk except the
  engine's export files.
#>
param(
  [string]$Ip,
  [int]$WebPort = 3000,
  [int]$ApiPort = 8000,
  [switch]$SkipBuild,
  [switch]$NoFirewall
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$uvicorn = Join-Path $repo ".venv\Scripts\uvicorn.exe"
if (-not (Test-Path $uvicorn)) { throw "no .venv with uvicorn in $repo (run scripts\ensure-venv.ps1)" }

if (-not $Ip) {
  # The interface with a default route is the one other computers reach.
  $cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } | Select-Object -First 1
  if (-not $cfg) { throw "no connected network adapter found; pass -Ip" }
  $Ip = $cfg.IPv4Address.IPAddress
}
$webUrl = "http://${Ip}:${WebPort}"
$apiUrl = "http://${Ip}:${ApiPort}"
Write-Host "`nThis PC: $Ip   web $webUrl   engine $apiUrl" -ForegroundColor Cyan

# --- firewall ----------------------------------------------------------------
if (-not $NoFirewall) {
  $missing = @()
  foreach ($p in @($WebPort, $ApiPort)) {
    if (-not (Get-NetFirewallRule -DisplayName "ATC FTS LAN test (port $p)" -ErrorAction SilentlyContinue)) { $missing += $p }
  }
  if ($missing.Count) {
    Write-Host "Allowing ports $($missing -join ', ') in from this network (administrator approval) ..."
    $cmds = $missing | ForEach-Object {
      "New-NetFirewallRule -DisplayName 'ATC FTS LAN test (port $_)' -Direction Inbound -Protocol TCP -LocalPort $_ -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null"
    }
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList "-NoProfile", "-Command", ($cmds -join "; ")
    if ($p.ExitCode -ne 0) { throw "firewall rules were not added (approval refused?)" }
  }
}

# --- front end, built for this address ---------------------------------------
$stamp = Join-Path $repo "web\.next-build\.lan-api-base"
if ($SkipBuild -and (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -ne $apiUrl)) {
  Write-Host "the existing build was made for $((Get-Content $stamp -Raw).Trim()), not $apiUrl; building again" -ForegroundColor Yellow
  $SkipBuild = $false
}
if (-not $SkipBuild) {
  Write-Host "`nBuilding the front end for $apiUrl (about a minute) ..."
  Push-Location (Join-Path $repo "web")
  try {
    $env:NEXT_PUBLIC_API_BASE = $apiUrl
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "front-end build failed" }
    Set-Content -Path $stamp -Value $apiUrl -Encoding ascii
  } finally {
    Remove-Item Env:NEXT_PUBLIC_API_BASE -ErrorAction SilentlyContinue
    Pop-Location
  }
}

# --- start both ---------------------------------------------------------------
Get-Process uvicorn -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# The browser on the other computer has the origin http://<ip>:3000; the engine
# must name it or every request is refused by CORS.
$env:WEB_ORIGIN = $webUrl
Start-Process -FilePath $uvicorn -ArgumentList "api.server:app", "--host", "0.0.0.0", "--port", "$ApiPort" -WorkingDirectory $repo
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "npm run start" -WorkingDirectory (Join-Path $repo "web")

function Wait-Url($url, $seconds) {
  foreach ($i in 1..($seconds * 2)) {
    try { $r = Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3; if ($r.StatusCode -eq 200) { return $true } } catch {}
    Start-Sleep -Milliseconds 500
  }
  $false
}
$apiOk = Wait-Url "$apiUrl/api/health" 60
$webOk = Wait-Url $webUrl 60
Write-Host ""
Write-Host ("engine {0}   web {1}" -f ($(if ($apiOk) { "up" } else { "NOT answering" }), $(if ($webOk) { "up" } else { "NOT answering" }))) -ForegroundColor $(if ($apiOk -and $webOk) { "Green" } else { "Red" })
if ($apiOk -and $webOk) {
  Write-Host "`nOn the other computer, open:   $webUrl" -ForegroundColor Green
  Write-Host "(same Wi-Fi/network; close the two console windows to stop)"
}
