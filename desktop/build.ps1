<#
  Build the desktop app from a clean checkout, on Windows or macOS:

      powershell -ExecutionPolicy Bypass -File desktop\build.ps1     # Windows
      pwsh desktop/build.ps1                                          # macOS

  1. checks every manifest carries the version in VERSION
  2. freezes the Python engine           -> desktop/sidecar/dist/atc-engine/
  3. exports the front end as static files -> web/.next-desktop/
  4. builds the shell and the installer  -> desktop/src-tauri/target/release/bundle/
     Windows: an NSIS installer.  macOS: the .app (plus the updater's .app.tar.gz)
     and a zip of the .app for people to download.
  5. assembles release/ (installer(s), signatures, latest.json)

  Needs: the repo's .venv with PyInstaller (or a system Python with the
  requirements), Node, Rust (rustup), and the platform's C++ toolchain
  (Visual Studio build tools / Xcode command-line tools). -SkipEngine /
  -SkipWeb reuse the previous output of that step.
#>
param(
  [switch]$SkipEngine,
  [switch]$SkipWeb
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$mac = $IsMacOS -eq $true
# The repo's venv on a developer machine; the runner's own Python in CI.
$python = if ($mac) { Join-Path $repo ".venv/bin/python" } else { Join-Path $repo ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { $python = if ($mac) { "python3" } else { "python" } }
$sep = [IO.Path]::PathSeparator
$env:Path = (Join-Path $HOME ".cargo/bin") + $sep + $env:Path
$version = (Get-Content (Join-Path $repo "VERSION") -Raw).Trim()

function Step($name, [scriptblock]$body) {
  Write-Host "`n=== $name ===" -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  & $body
  if ($LASTEXITCODE -ne 0) { throw "$name failed (exit $LASTEXITCODE)" }
  Write-Host ("    done in {0:N0} s" -f $sw.Elapsed.TotalSeconds)
}

Step "version check" {
  & $python (Join-Path $repo "scripts/sync_version.py") --check
}

Step "installer licence text" {
  & $python (Join-Path $repo "scripts/make_installer_license.py")
}

Step "third-party notices" {
  & $python (Join-Path $repo "scripts/gen_notices.py")
}

if (-not $SkipEngine) {
  Step "engine (PyInstaller)" {
    Push-Location $repo
    try {
      & $python -m PyInstaller --noconfirm --clean --log-level WARN `
        (Join-Path $repo "desktop/sidecar/engine.spec") `
        --distpath (Join-Path $repo "desktop/sidecar/dist") `
        --workpath (Join-Path $repo "desktop/sidecar/build")
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
      $env:NEXT_PUBLIC_APP_VERSION = $version
      # Shown in the About dialog as "What's new in this version". Read as
      # UTF-8 explicitly: Windows PowerShell 5.1 would take a BOM-less file
      # for ANSI and turn a dash or an accent into garbage.
      $env:NEXT_PUBLIC_RELEASE_NOTES = (Get-Content (Join-Path $repo "RELEASE_NOTES.md") -Raw -Encoding UTF8).Trim()
      npx next build
      # The desktop front end gets its static data from the engine (which
      # bundles the same folder), so it does not ship a second copy.
      $exportData = Join-Path $repo "web/.next-desktop/data"
      if (Test-Path $exportData) { Remove-Item -Recurse -Force $exportData }
    } finally {
      Remove-Item Env:DESKTOP_BUILD, Env:NEXT_DIST_DIR, Env:NEXT_PUBLIC_API_BASE, Env:NEXT_PUBLIC_APP_VERSION, Env:NEXT_PUBLIC_RELEASE_NOTES -ErrorAction SilentlyContinue
      Pop-Location
    }
  }
}

# The updater only installs releases signed with this key (the public half is
# in tauri.conf.json). CI passes it as a secret; locally it is read from the
# key file outside the repo.
if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
  $keyFile = Join-Path $HOME ".tauri/atc-fts-updater.key"
  if (-not (Test-Path $keyFile)) { throw "updater signing key not found: $keyFile (or set TAURI_SIGNING_PRIVATE_KEY)" }
  $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content $keyFile -Raw).Trim()
}
if ($null -eq $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "" }

Step "shell + installer (Tauri)" {
  Push-Location $PSScriptRoot
  try {
    if ($mac) {
      # The .app only (no .dmg: its bundler needs an interactive desktop).
      # createUpdaterArtifacts adds the signed .app.tar.gz the updater uses.
      npx tauri build --bundles app
    } else {
      npx tauri build
    }
  } finally { Pop-Location }
}

if ($mac) {
  Step "zip of the .app (the download for people)" {
    $bundle = Join-Path $PSScriptRoot "src-tauri/target/release/bundle/macos"
    $app = Get-ChildItem $bundle -Filter "*.app" -Directory | Select-Object -First 1
    if (-not $app) { throw "no .app in $bundle" }
    $release = Join-Path $repo "release"
    New-Item -ItemType Directory -Force $release | Out-Null
    $zip = Join-Path $release "ATC-FTS_${version}_macos-arm64.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    # ditto keeps the bundle's permissions and metadata, which a plain zip
    # tool would lose; Finder unpacks it to the .app.
    & ditto -c -k --keepParent $app.FullName $zip
  }
}

Step "release folder (installers + latest.json)" {
  & $python (Join-Path $repo "scripts/make_update_manifest.py") --notes-file (Join-Path $repo "RELEASE_NOTES.md")
}

Write-Host "`n=== artifacts ===" -ForegroundColor Cyan
Get-ChildItem (Join-Path $repo "release") |
  ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB) }
