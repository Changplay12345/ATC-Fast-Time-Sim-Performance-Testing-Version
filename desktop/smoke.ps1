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
    5. the window opens, and closing it ends the app and the engine
    6. the app still ends if its internal helper window was closed first
    7. killing the app takes the engine with it (the parent watch)
  Exits non-zero if any check failed.

  Closing the window: do NOT use .NET's CloseMainWindow() for this. The
  process has two top-level windows - the real one (class "Tauri Window") and
  an invisible helper (class "Tao Thread Event Target") - and CloseMainWindow
  picks whichever is first in the stacking order, so with the app minimised
  or behind another window it closes the helper instead. Address the real
  window by its class, as below.
#>
param(
  [switch]$Install,
  [string]$Exe
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $repo "VERSION")).Trim()
$failed = 0

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class SmokeWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  // The process's top-level window of the given class, or zero.
  public static IntPtr Find(uint want, string cls) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want) {
        var c = new StringBuilder(256); GetClassName(h, c, 256);
        if (c.ToString() == cls) { found = h; return false; }
      }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  public static bool Close(IntPtr h) { return PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero); } // WM_CLOSE
}
"@
$REAL_WINDOW = "Tauri Window"
$HELPER_WINDOW = "Tao Thread Event Target"

function Check($name, [bool]$ok, $detail = "") {
  if ($ok) { Write-Host "  ok    $name $detail" -ForegroundColor Green }
  else { Write-Host "  FAIL  $name $detail" -ForegroundColor Red; $script:failed++ }
}
function Status($url) {
  try { (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 10).StatusCode }
  catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
}
function Stop-All {
  Get-Process atc-fts-desktop, atc-engine -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}
# Starts the app and waits until its engine is listening. Returns both.
function Start-App {
  $app = Start-Process -FilePath $Exe -PassThru
  $engine = $null
  foreach ($i in 1..240) {
    Start-Sleep -Milliseconds 250
    $engine = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "atc-engine.exe" -and $_.ParentProcessId -eq $app.Id } | Select-Object -First 1
    if ($engine -and (Get-NetTCPConnection -OwningProcess $engine.ProcessId -State Listen -ErrorAction SilentlyContinue)) { break }
  }
  @{ App = $app; Engine = $engine }
}
function Wait-Window($app, $class, $seconds = 30) {
  foreach ($i in 1..($seconds * 4)) {
    $h = [SmokeWin]::Find([uint32]$app.Id, $class)
    if ($h -ne [IntPtr]::Zero) { return $h }
    Start-Sleep -Milliseconds 250
  }
  [IntPtr]::Zero
}
# True once the app has exited and no engine process is left.
function Wait-Gone($app, $seconds) {
  foreach ($i in 1..($seconds * 4)) {
    if ($app.HasExited -and -not (Get-Process atc-engine -ErrorAction SilentlyContinue)) { return $true }
    Start-Sleep -Milliseconds 250
  }
  $false
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

Stop-All

# --- first launch: the engine, then a normal close -------------------------
Write-Host "starting $Exe"
$run = Start-App
$app = $run.App; $engine = $run.Engine
Check "engine started by the app" ($null -ne $engine)
if (-not $engine) { Stop-All; exit 1 }

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

$window = Wait-Window $app $REAL_WINDOW
Check "the window opens" ($window -ne [IntPtr]::Zero)
if ($window -ne [IntPtr]::Zero) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  [void][SmokeWin]::Close($window)
  $ok = Wait-Gone $app 10
  Check "closing the window ends the app and the engine" $ok "($($sw.ElapsedMilliseconds) ms)"
}
Stop-All

# --- second launch: a close that hits the helper window first --------------
# That used to leave a windowless process holding the engine for ever.
$run = Start-App
$app = $run.App
$window = Wait-Window $app $REAL_WINDOW
$helper = Wait-Window $app $HELPER_WINDOW 5
if ($window -ne [IntPtr]::Zero -and $helper -ne [IntPtr]::Zero) {
  [void][SmokeWin]::Close($helper)
  Start-Sleep -Seconds 1
  $sw = [Diagnostics.Stopwatch]::StartNew()
  [void][SmokeWin]::Close($window)
  $ok = Wait-Gone $app 15
  Check "the app ends even if its helper window was closed first" $ok "($($sw.ElapsedMilliseconds) ms)"
} else {
  Check "the app ends even if its helper window was closed first" $false "(windows not found)"
}
Stop-All

# --- third launch: the parent watch ----------------------------------------
# A hard kill of the app must not leave an engine behind.
$run = Start-App
Stop-Process -Id $run.App.Id -Force
$gone = $false
foreach ($i in 1..40) {
  Start-Sleep -Milliseconds 250
  if (-not (Get-Process atc-engine -ErrorAction SilentlyContinue)) { $gone = $true; break }
}
Check "engine exits when the app is killed" $gone
Stop-All

if ($failed) { Write-Host "`n$failed check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nsmoke test passed" -ForegroundColor Green
