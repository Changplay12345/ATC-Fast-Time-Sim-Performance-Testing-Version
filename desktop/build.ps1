<#
  Build the desktop app from a clean checkout:

      powershell -ExecutionPolicy Bypass -File desktop\build.ps1

  1. checks every manifest carries the version in VERSION
  2. freezes the Python engine           -> desktop\sidecar\dist\atc-engine\
  3. exports the front end as static files -> web\.next-desktop\
  4. builds the shell and the installer  -> desktop\src-tauri\target\release\bundle\nsis\

  Needs: the repo's .venv with PyInstaller, Node, Rust (rustup) and the Visual
  Studio C++ build tools. -SkipEngine / -SkipWeb reuse the previous output of
  that step (handy when only the shell changed).
#>
param(
  [switch]$SkipEngine,
  [switch]$SkipWeb
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
# The repo's venv on a developer machine; the runner's own Python in CI.
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$env:Path = "$env:USERPROFILE\.cargo\bin;" + $env:Path

function Step($name, [scriptblock]$body) {
  Write-Host "`n=== $name ===" -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  & $body
  if ($LASTEXITCODE -ne 0) { throw "$name failed (exit $LASTEXITCODE)" }
  Write-Host ("    done in {0:N0} s" -f $sw.Elapsed.TotalSeconds)
}

Step "version check" {
  & $python (Join-Path $repo "scripts\sync_version.py") --check
}

if (-not $SkipEngine) {
  Step "engine (PyInstaller)" {
    Push-Location $repo
    try {
      & $python -m PyInstaller --noconfirm --clean --log-level WARN `
        desktop\sidecar\engine.spec `
        --distpath desktop\sidecar\dist --workpath desktop\sidecar\build
    } finally { Pop-Location }
  }
}

if (-not $SkipWeb) {
  Step "front end (static export)" {
    Push-Location (Join-Path $repo "web")
    try {
      $env:DESKTOP_BUILD = "1"
      $env:NEXT_DIST_DIR = ".next-desktop"
      # The engine's real address is injected by the shell at launch; this is
      # only the fallback baked into the bundle.
      $env:NEXT_PUBLIC_API_BASE = "http://127.0.0.1:8765"
      $env:NEXT_PUBLIC_APP_VERSION = (Get-Content (Join-Path $repo "VERSION")).Trim()
      npx next build
    } finally {
      Remove-Item Env:DESKTOP_BUILD, Env:NEXT_DIST_DIR, Env:NEXT_PUBLIC_API_BASE, Env:NEXT_PUBLIC_APP_VERSION -ErrorAction SilentlyContinue
      Pop-Location
    }
  }
}

# The updater only installs releases signed with this key (the public half is
# in tauri.conf.json). CI passes it as a secret; locally it is read from the
# key file outside the repo.
if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
  $keyFile = Join-Path $env:USERPROFILE ".tauri\atc-fts-updater.key"
  if (-not (Test-Path $keyFile)) { throw "updater signing key not found: $keyFile (or set TAURI_SIGNING_PRIVATE_KEY)" }
  $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content $keyFile -Raw).Trim()
}
if ($null -eq $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "" }

Step "shell + installer (Tauri)" {
  Push-Location $PSScriptRoot
  try { npx tauri build } finally { Pop-Location }
}

Step "release folder (installer + latest.json)" {
  & $python (Join-Path $repo "scripts\make_update_manifest.py") --notes-file (Join-Path $repo "RELEASE_NOTES.md")
}

Write-Host "`n=== artifacts ===" -ForegroundColor Cyan
Get-ChildItem (Join-Path $repo "release") |
  ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB) }
