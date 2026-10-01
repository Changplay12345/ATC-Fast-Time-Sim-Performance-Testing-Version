<#
  Smoke test of the desktop app - run after every build, and by CI before a
  release is published.

      powershell -ExecutionPolicy Bypass -File desktop\smoke.ps1                 # the built exe
      powershell -ExecutionPolicy Bypass -File desktop\smoke.ps1 -Install        # install release\*.exe first

  Checks, in order:
    1. the app starts and brings its engine up
    2. the engine listens on 127.0.0.1 only, on a port it picked itself
    3. /api/health answers, with the same version as VERSION
    4. the API refuses a request without the session token; the docs are off
    5. killing the app takes the engine with it (the parent watch)
  Exits non-zero on the first failure.
#>
param(
  [switch]$Install,
  [string]$Exe
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $repo "VERSION")).Trim()
$failed = 0

function Check($name, [bool]$ok, $detail = "") {
  if ($ok) { Write-Host "  ok    $name $detail" -ForegroundColor Green }
  else { Write-Host "  FAIL  $name $detail" -ForegroundColor Red; $script:failed++ }
}
function Status($url) {
  try { (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 10).StatusCode }
  catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
}

if ($Install) {
  $setup = Get-ChildItem (Join-Path $repo "release\*-setup.exe") | Select-Object -First 1
  if (-not $setup) { throw "no installer in release\ - run desktop\build.ps1" }
  Write-Host "installing $($setup.Name) ..."
  $p = Start-Process -FilePath $setup.FullName -ArgumentList "/S" -PassThru -Wait
  Check "silent install" ($p.ExitCode -eq 0) "(exit $($p.ExitCode))"
  $Exe = Join-Path $env:LOCALAPPDATA "ATC Fast-Time Simulation Tool\atc-fts-desktop.exe"
}
if (-not $Exe) { $Exe = Join-Path $PSScriptRoot "src-tauri\target\release\atc-fts-desktop.exe" }
if (-not (Test-Path $Exe)) { throw "app not found: $Exe" }

Get-Process atc-fts-desktop, atc-engine -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

Write-Host "starting $Exe"
$app = Start-Process -FilePath $Exe -PassThru
$engine = $null
foreach ($i in 1..240) {
  Start-Sleep -Milliseconds 250
  $engine = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "atc-engine.exe" -and $_.ParentProcessId -eq $app.Id } | Select-Object -First 1
  if ($engine -and (Get-NetTCPConnection -OwningProcess $engine.ProcessId -State Listen -ErrorAction SilentlyContinue)) { break }
}
Check "engine started by the app" ($null -ne $engine)
if (-not $engine) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue; exit 1 }

$listen = @(Get-NetTCPConnection -OwningProcess $engine.ProcessId -State Listen)
$port = $listen[0].LocalPort
Check "listens on loopback only" (@($listen | Where-Object { $_.LocalAddress -ne "127.0.0.1" }).Count -eq 0) "(127.0.0.1:$port)"

$health = $null
foreach ($i in 1..120) {
  try { $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 5; break } catch { Start-Sleep -Milliseconds 500 }
}
Check "health answers" ($null -ne $health -and $health.ok)
Check "engine version matches VERSION" ($health.version -eq $version) "($($health.version) vs $version)"
Check "engine is in local mode" ($health.mode -eq "local")
Check "API refuses a request without the token" ((Status "http://127.0.0.1:$port/api/cat62_reference") -eq 401)
Check "interactive docs are off" ((Status "http://127.0.0.1:$port/docs") -eq 404)

# The parent watch: a hard kill of the app must not leave an engine behind.
Stop-Process -Id $app.Id -Force
$gone = $false
foreach ($i in 1..40) {
  Start-Sleep -Milliseconds 250
  if (-not (Get-Process atc-engine -ErrorAction SilentlyContinue)) { $gone = $true; break }
}
Check "engine exits when the app is killed" $gone
Get-Process atc-engine -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if ($failed) { Write-Host "`n$failed check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nsmoke test passed" -ForegroundColor Green
