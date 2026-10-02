# ATC Fast-Time Simulation Tool — Desktop Release Plan

Status: **planning, no code**. Written 2026-10-02 against commit `4aaea05`.

A second distribution of the tool: a desktop application users install and run on
their own machine, so trajectory generation, batch runs and conflict scans use their
CPU and GPU instead of the hosted Render backend. The web version (Vercel + Render)
stays as it is. Both are built from this repository.

## 0. Decisions already made

| # | Decision | Choice |
|---|---|---|
| 1 | Product name | **ATC Fast-Time Simulation Tool**. Used for the signing certificate's publisher line, the installer, the Start-menu entry, the data folder (`%LOCALAPPDATA%\ATC Fast-Time Simulation Tool`) and the EULA |
| 2 | Update channels | **Stable only.** The manifest URL still contains a channel segment (`/update/stable/…`) so a beta channel can be added later without changing the settings schema |
| 3 | Backend mode | **Local only for now**, with the local/hosted switch kept in code (no UI) so a future migration to a hosted engine, or a hybrid, needs no front-end rewrite |
| 4 | Data licensing | **User accounts, later.** Not in scope now; the data endpoint accepts a bearer token from day one and the app has a place for one |
| 5 | macOS | **Apple Silicon only.** Secondary platform; starts after Windows is shipping |
| 6 | Telemetry | **Opt-in crash reports (Sentry), default off**, plus local logs and an "Export logs" button |
| 7 | Hardware floor | **Minimum 4 cores / 8 GB RAM; recommended 8 cores / 16 GB; WebGL2 GPU**, with the existing `!gpu` fallback below that |
| — | Platform priority | **Windows first and must be flawless. macOS second.** Linux only on request |
| A | Release hosting | **The repository becomes public**; releases and the update manifest live on its GitHub Releases |
| B | AIXM-derived procedure layers | **Ship them in the app for now.** They are built as their own data pack (`procedures`) from day one, so they can later move behind licensed accounts without touching the app |
| C | Publisher identity | **BearCat AEL Co.** Held in one place (`desktop/brand.json`: publisher, product name, support URL) and read by the installer, EULA, About box and manifests, so it can change with one edit and one release |
| — | Code signing | **None for now.** Windows shows a SmartScreen "unknown publisher" warning at install and at each update; accepted. Revisit when it hurts (see 4.3). macOS signing is deferred to Phase 3, where it is effectively mandatory |

## 1. Architecture

### 1.1 Options compared

| | Tauri 2 + Python sidecar | Electron + Python sidecar | Local launcher only |
|---|---|---|---|
| Installer size | ~70–90 MB (shell ~10 MB + engine) | ~170–190 MB (shell ~100 MB + engine) | ~70 MB, but no installer story |
| RAM at rest | ~150 MB shell + WebView, +120 MB engine | ~300 MB shell, +120 MB engine | engine only; the user's browser is extra |
| WebGL / deck.gl | Windows: WebView2 = Chromium, same as Chrome. macOS: WKWebView (Safari), WebGL2 works, less tested with deck.gl | Identical Chromium everywhere | Whatever browser the user has |
| Bundling GDAL/PROJ data | Same sidecar either way: PyInstaller collects `pyproj` (built-in hook) and `pyogrio` data; verify in the spike | Same | Same |
| Auto-update | Built-in updater, signed manifests, background download, install on restart | electron-updater, mature, same features | None |
| Code signing | Optional on Windows (warning if absent); required on macOS | Same | Same |
| Developer effort | Rust shell is small (<500 lines); most work is the sidecar and CI | JS shell, similar size; heavier runtime to keep patched | Lowest, but no product |
| Risk | WKWebView rendering on macOS | Size, RAM, Chromium security updates | Not a product: open localhost server, no updates, browser quirks |

### 1.2 Recommendation

**Tauri 2 shell + PyInstaller one-folder sidecar + Next.js static export.**

- Windows, the primary platform, gets Chromium rendering through WebView2 — the
  same engine the GPU map was tuned on this week.
- The built-in updater gives the Steam-style flow (check on launch and
  periodically, background download, install on restart) with signed manifests.
- macOS is first-class in Tauri; the only macOS-specific risk is WKWebView, which
  the existing `!gpu` fallback covers in the worst case, and which Phase 3 tests
  before anything ships there.
- If WKWebView proves unworkable, the escape hatch is Electron; the sidecar, the
  updater design and the data packs all carry over unchanged.

**Engine packaging:** PyInstaller **one-folder** (not one-file). Starts in
~1 s instead of unpacking to a temp folder each launch, triggers fewer
antivirus false positives, and is what macOS notarisation handles best. Nuitka is
a later optimisation if size or start-up time matters; embedded Python is too
fragile for the GDAL/PROJ stack.

**Front end:** `next.config` `output: 'export'`. Verified: the app has no API
routes, no SSR-only features, no `next/image`; its one non-API fetch
(`/data/airports/runway_default.csv`) is a static file. Nothing breaks. Build-time
`NEXT_PUBLIC_API_BASE` is replaced by runtime config (below); the web build keeps
using the env var through the same abstraction.

### 1.3 Diagram

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Desktop app (installed per user)                                         │
│                                                                           │
│  ┌──────────────────────┐   spawn, env: PORT, TOKEN   ┌─────────────────┐ │
│  │  Tauri shell (Rust)  │ ─────────────────────────▶ │  Engine sidecar │ │
│  │  · window + WebView  │ ◀───────────────────────── │  (PyInstaller)  │ │
│  │  · updater           │   health / exit, PID file   │  FastAPI+uvicorn│ │
│  │  · settings, logs    │                             │  trajectory_sim │ │
│  │  · sidecar lifecycle │                             │  127.0.0.1:PORT │ │
│  └─────────┬────────────┘                             └────────▲────────┘ │
│            │ serves static export + runtime config              │          │
│            │ (window.__APP_CONFIG__ = {apiBase, token, mode})   │          │
│  ┌─────────▼────────────┐   fetch + Authorization: Bearer       │          │
│  │  Front end (Next.js  │ ──────────────────────────────────────┘          │
│  │  static export, same │                                                   │
│  │  code as the web)    │ ──▶ Esri/CARTO tiles (internet)                   │
│  └──────────────────────┘ ──▶ data-pack manifest + files (internet)         │
└───────────────────────────────────────────────────────────────────────────┘
                 │ check for updates (on launch, every 4 h, manual)
                 ▼
┌───────────────────────────────┐      ┌────────────────────────────────────┐
│  Update host                  │      │  Data host                         │
│  GitHub Releases (Phase 1)    │      │  Cloudflare R2 + Worker            │
│  → Cloudflare R2 + Worker     │      │  data-manifest.json (signed)       │
│    (Phase 4, staged rollout)  │      │  packs: navdata, AIP, thresholds,  │
│  latest.json (signed)         │      │  FIR, performance — versioned      │
│  installers / .app bundles    │      │  bearer token slot (accounts later)│
└───────────────────────────────┘      └────────────────────────────────────┘
```

### 1.4 How the front end finds the engine

1. The shell picks a free port on `127.0.0.1`, generates a 32-byte random
   session token, and starts the sidecar with both in its environment.
2. The sidecar binds to `127.0.0.1` only, requires `Authorization: Bearer
   <token>` on every request except `/api/health`, and has **no CORS
   middleware** in local mode (same origin, none needed).
3. The shell polls `/api/health` (up to 30 s) and only then shows the window.
   `/api/health` reports the engine version; a mismatch with the shell version
   is a hard error ("reinstall").
4. The shell injects `window.__APP_CONFIG__ = { mode: "local", apiBase, token,
   version }` into the page. `web/lib/backend.ts` reads it when present and
   falls back to `NEXT_PUBLIC_API_BASE` (web build). All `fetch` calls go
   through one helper that adds the token.
5. On window close the shell sends the sidecar SIGTERM, waits 5 s, then kills
   it. A PID file in the data folder lets the next launch sweep an orphan left
   by a crash. The sidecar also exits on its own if its parent dies (the
   existing worker-exit mechanism, extended to the main process).

### 1.5 One codebase, two products

A runtime `backendMode` with three values: `local` (desktop), `hosted` (web
today), `hybrid` (desktop with a hosted engine selected — code only, no UI
until needed). Tests: vitest runs against a stubbed `__APP_CONFIG__` for both
modes; pytest covers the token middleware and the bind address. The web build
is unchanged in behaviour.

## 2. Installer

### 2.1 Windows (primary)

| Item | Choice |
|---|---|
| Format | **NSIS `.exe`** (Tauri's default), per-user, no admin prompt. `.msi` is offered later only if an organisation asks for silent deployment |
| Install path | `%LOCALAPPDATA%\Programs\ATC Fast-Time Simulation Tool\` |
| Shortcuts | Start menu always; desktop shortcut optional (checkbox, default on) |
| File associations | `.atcsim` (scenario/project file, new, JSON) → opens the app. `.gpkg` is **not** claimed (QGIS and others own it) |
| Prerequisites | WebView2 runtime (present on Windows 10 1803+ and 11; the installer bootstraps it if missing). VC++ 2015–2022 redistributable (bundled, silent). **No Python, no Node** |
| Uninstall | Removes the program folder and shortcuts. **Keeps** `%LOCALAPPDATA%\ATC Fast-Time Simulation Tool\data` (settings, logs, data packs, scenarios) unless the user ticks "Also remove my data" |
| Target size | **≤ 90 MB download**, ≤ 300 MB on disk |

Reaching the size: PyInstaller excludes (`matplotlib`, `IPython`, `pytest`,
`tkinter`, pandas and numpy test packages), `--strip` on Linux/mac, UPX **off**
(it inflates antivirus false positives), ship only the PROJ and GDAL data files
actually used (PROJ: `proj.db` only; GDAL: no sample data). Expected engine folder
~180 MB on disk, ~60–70 MB compressed.

### 2.2 macOS (secondary, Phase 3)

`.dmg` with drag-to-Applications, Apple Silicon only, signed with Developer ID and
notarised; the sidecar binary and every `.dylib` in it signed with the hardened
runtime. Data in `~/Library/Application Support/ATC Fast-Time Simulation Tool/`.

### 2.3 Linux

Not planned. AppImage can be added in a day if a user asks; it needs no signing.

## 3. Auto-update

### 3.1 Mechanism

| Item | Choice |
|---|---|
| Updater | **Tauri updater plugin**. Manifest `latest.json` lists version, notes, publish date and per-platform download URL + signature |
| Signing | **minisign key pair** generated once, private key in CI secrets only. The app ships the public key; an update whose signature doesn't verify is discarded. This is independent of code signing and protects against a hijacked host |
| Host, Phase 1 | **GitHub Releases** on a *public* release repository (see open question A). Free, CDN-backed |
| Host, Phase 4 | **Cloudflare R2 + a Worker** serving `latest.json`. Enables staged rollout (the Worker returns the new version to N % of installation IDs), kill-switch for a bad release, and per-platform stats |
| Channels | Stable. URL layout `…/update/{channel}/{target}/{arch}/latest.json` so beta is an additive change |
| Delta vs full | Full downloads. ~80 MB per update is acceptable on Wi-Fi; deltas can come later via the Worker if update frequency becomes high |
| Forced update | A `minimumVersion` field in the manifest. Below it, the app updates before it will start a simulation (used only for critical fixes, e.g. a wrong-separation bug) |
| Rollback | The installer keeps the previous version's installer in the data folder; if the new version fails its own health check twice in a row at launch, the shell offers "Reinstall previous version" |

### 3.2 UX (Steam-like)

- Check on launch, then every 4 hours while running, plus **Help → Check for
  updates** at any time.
- If an update exists: download in the background, show a small badge
  "Update ready — restart to install". No modal, no interruption.
- **Never install while a simulation or batch generation is running.** The
  prompt appears only when the engine is idle.
- "Install and restart" applies the update; "Later" defers it to next launch.
- Release notes are shown from the manifest; the full changelog lives in
  `CHANGELOG.md` and on the GitHub release.

### 3.3 Data updates (separate from app updates)

- A **data pack** is a versioned archive: navdata (AIP fixes/airways/
  procedures), threshold elevations, FIR and sector polygons, aircraft
  performance tables, CAT62 reference. Packs are versioned by AIRAC cycle
  (`2026-09-03`) plus a build number.
- `data-manifest.json`, signed with the same minisign key, lists the current
  pack, its SHA-256 and size, and the minimum app version it needs.
- The app ships with the current pack bundled, checks the manifest on launch,
  downloads a newer pack in the background, verifies the hash and signature,
  swaps it in on next engine start. Old packs are kept (last 2) for rollback.
- The manifest request carries `Authorization: Bearer <token>` when a token is
  present. Today the endpoint ignores it; when accounts arrive (Decision 4) the
  Worker starts checking it, and the app shows "sign in to update data" rather
  than failing. No UI is built now.
- `/api/health` reports `data_version` so the About box and bug reports show
  which cycle is loaded.

### 3.4 User data and settings migration

- Settings file `settings.json` and scenarios carry `schemaVersion`.
- On launch the shell runs forward-only migrations (`v1 → v2 → …`); a backup
  `settings.v1.bak.json` is written before each one.
- Scenario files (`.atcsim`) embed the app and data versions they were saved
  with; opening one from a newer app is refused with a clear message.

## 4. Release engineering

### 4.1 Versioning

- **SemVer**, one source: a `VERSION` file at the repo root.
- CI writes it into `desktop/src-tauri/tauri.conf.json`, `web`
  (`NEXT_PUBLIC_APP_VERSION` at build), and `api/version.py`; `/api/health`
  returns it. A pre-commit check fails if any of the three disagree.
- Git tag `v1.2.3` triggers a release build.

### 4.2 CI/CD (GitHub Actions)

| Job | Runner | Steps |
|---|---|---|
| `test` | ubuntu | pytest, vitest, tsc, version-consistency check |
| `engine-win` | windows-latest | PyInstaller build, run the 40-flight batch against the built sidecar, upload artifact |
| `desktop-win` | windows-latest | Next static export, Tauri build with the engine artifact, upload. (Code-signing step present but disabled until a certificate exists) |
| `engine-mac` / `desktop-mac` | macos-14 (arm64) | Same, with Developer ID signing + notarisation + stapling. "Allowed to fail" until Phase 3 |
| `release` | ubuntu | On tag: create GitHub Release, attach installers, generate and sign `latest.json`, publish; release notes from `CHANGELOG.md` |
| `smoke` | windows-latest (fresh VM image) | Download the published installer, install silently, launch, wait for `/api/health`, generate 5 flights, uninstall. Must pass before the manifest is promoted |

### 4.3 Code signing — deferred by decision

**Windows: unsigned for now.** Consequences, so they are not a surprise:

- The installer shows SmartScreen's "Windows protected your PC — unknown
  publisher" screen; the user clicks *More info → Run anyway*. The download
  page and the first-run guide show a screenshot of this.
- **Every auto-update shows it too**, because the updater runs the new
  installer. The update badge text says so ("Windows will ask you to confirm").
- Some antivirus products score unsigned PyInstaller binaries higher; the
  mitigations in 5.5 (one-folder, no UPX, vendor submissions) still apply.
- Nothing else breaks: the updater's own minisign signature (3.1) is separate
  from code signing and stays in place.

When to revisit: when the warning costs users, or when an organisation asks.
Then **Azure Trusted Signing** (~US$10/month, no hardware token, works from CI)
is the choice; OV certificates (~$200/yr) need a hardware token and start with
no reputation; EV (~$300–400/yr) gives instant reputation. The CI signing step
is written now and switched on by adding the secrets. The certificate is
issued to BearCat AEL Co.

**macOS: deferred to Phase 3, but not optional there.** Recent macOS refuses
to open an unsigned, un-notarised app ("damaged, move to Bin") unless the user
clears the quarantine attribute by hand — not something to ask of users.
Budget the **Apple Developer Program ($99/yr)** for Phase 3.

### 4.4 Pre-release smoke tests

Beyond CI's automated smoke: before each stable release, a manual pass on
(a) a clean Windows 11 VM, (b) a clean Windows 10 22H2 VM, (c) a machine with
Windows Defender on and one third-party antivirus, (d) a laptop with integrated
graphics (checks the `!gpu` fallback and WebView2 on Intel/AMD iGPUs). Checklist
in `desktop/RELEASE_CHECKLIST.md`.

## 5. Operational and professional concerns

### 5.1 Logs, crash reports, privacy

- Shell and engine write rotating logs to the data folder (`logs/`, 7 days,
  10 MB each). **Help → Export logs** zips them with a system summary (OS,
  GPU, versions, no file paths outside the app).
- **Opt-in** Sentry for the shell and the sidecar, default off, toggle in
  Settings with the exact list of what is sent. Reports are scrubbed of user
  paths and scenario contents. A `PRIVACY.md` states this and ships in the
  About box.

### 5.2 Licensing

- **Our EULA** (`EULA.md`): shown once at first launch, linked in About.
- **Third-party notices** (`THIRD_PARTY_NOTICES.md`, generated in CI from
  `pip-licenses` and `license-checker`): GDAL and PROJ (MIT / X11), deck.gl
  (MIT), Leaflet (BSD-2), PyInstaller bootloader (GPL with the bootloader
  exception — permits closed-source distribution; note it explicitly),
  pandas/numpy/shapely (BSD), FastAPI/uvicorn (MIT/BSD).
- **Tiles:** Esri's terms permit use in applications with attribution (kept on
  the map); CARTO's require an API key per application — the desktop build
  ships **without** a CARTO key (Esri fallback only) until a key with
  redistribution terms is arranged.
- **Navdata:** the AIP-derived data is public; the AIXM-derived procedure
  layers in `web/public/data/aixm/` come from a licensed export ("internal and
  demonstration use only" per `.gitignore`). **This must be resolved before
  public distribution** — see open question B.

### 5.3 Offline behaviour

Wi-Fi is required by decision; the app says so on first launch. Without
internet: generation, scans and playback work; the map shows a grey
background with a "basemap unavailable" note (no bundled tiles — a Thailand
tile bundle at zooms 5–12 would be ~400 MB and is not justified); updates and
data packs wait until the connection returns.

### 5.4 Security

- Engine binds `127.0.0.1` only; per-session bearer token; no CORS middleware
  in local mode; the token never leaves the machine.
- The sidecar exposes only the routes the app needs; `/docs` and `/openapi.json`
  are disabled in local mode.
- Update and data manifests are signature-verified; downloads are hash-verified
  before use.
- No auto-execution of downloaded content; data packs are data files only.

### 5.5 Antivirus false positives

One-folder PyInstaller, UPX off, code-signed sidecar and shell, a stable
publisher certificate, and submitting each release to Microsoft Defender and the
major vendors' false-positive portals. Expect a few reports in the first
months; the opt-in crash report includes the antivirus name when a launch
fails so the pattern is visible.

### 5.6 Hardware

Stated: minimum 4 cores / 8 GB / WebGL2 GPU; recommended 8 cores / 16 GB. The
engine sizes its worker pool from the machine (cores − 1, max 8), so the floor
runs 3 workers (~1 minute for a 2,000-flight day) and the recommended spec runs
7. Below WebGL2, the map falls back to the Leaflet renderer automatically.

### 5.7 Support surface in the app

**Help → About** shows app, engine and data versions, the install folder, and
buttons: *Check for updates*, *Check for data updates*, *Export logs*, *Open
data folder*, *View licences*.

## 6. Phased roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Spike (2 weeks)** | PyInstaller build of the engine on Windows; Tauri shell loading the static export; deck.gl in WebView2; sidecar lifecycle; size measurement | The built sidecar generates the 1,990-flight file on a clean Windows 11 VM; the map pans at ≥ 60 fps in WebView2; installer prototype ≤ 110 MB; no Python on the VM |
| **1 — Windows MVP with auto-update (4–6 weeks)** | Signed NSIS installer, per-user; runtime config; token auth; updater on stable channel via GitHub Releases; logs + Export logs; EULA; CI release pipeline; smoke job | Two consecutive public releases where v(n) updates itself to v(n+1) on the four smoke machines; zero SmartScreen "unknown publisher" after signing; uninstall leaves data |
| **2 — Data packs (2–3 weeks)** | Pack format, signed manifest, background download, verify, swap, rollback; `data_version` in health; About box | A new AIRAC pack reaches an installed app without an app update; a corrupted pack is rejected; token header plumbed but unused |
| **3 — macOS Apple Silicon (3–4 weeks)** | Signed + notarised `.dmg`; WKWebView validation of the GPU map; mac CI required | Same smoke checklist passes on two Apple Silicon Macs; updater works on macOS; `!gpu` fallback verified |
| **4 — Hardening (ongoing)** | Opt-in Sentry; R2 + Worker update host with staged rollout and kill-switch; forced-update path; antivirus submissions; `hybrid` mode behind a flag; accounts groundwork on the data Worker | A bad release can be halted within minutes; crash rate visible; hybrid mode passes the same tests as local |

## 7. Repository changes per phase

**Phase 0–1**
- `desktop/brand.json` (new): publisher "BearCat AEL Co", product name,
  support URL, copyright line. CI templates it into `tauri.conf.json`, the
  NSIS installer, the EULA header and the About box — the single place the
  identity changes (Decision C).
- `desktop/` (new): `src-tauri/` (Rust shell: `main.rs`, `sidecar.rs`,
  `updater.rs`, `config.rs`, `tauri.conf.json`, icons), `sidecar/`
  (`engine.spec` for PyInstaller, `hooks/`, `build_engine.py`), `installer/`
  (NSIS template, EULA), `RELEASE_CHECKLIST.md`.
- `api/server.py`: `--bind`/`ATC_BIND` (default `127.0.0.1` in local mode),
  bearer-token middleware (`ATC_SESSION_TOKEN`), docs disabled in local mode,
  `version` and `data_version` in `/api/health`; `api/version.py`.
- `web/next.config.mjs`: `output: 'export'` when `DESKTOP_BUILD=1`;
  `web/lib/backend.ts` (runtime config, mode, token, one fetch helper) and
  `web/lib/api.ts` routed through it; `web/lib/backend.test.ts`.
- `VERSION`, `CHANGELOG.md`, `EULA.md`, `PRIVACY.md`, `THIRD_PARTY_NOTICES.md`
  (generated).
- `.github/workflows/desktop-release.yml`, `desktop-ci.yml`; `scripts/
  check_version.py`, `scripts/gen_notices.py`.

**Phase 2**
- `data/` (new): `pack.json` schema, `build_pack.py` (assembles packs from
  `web/public/data` + `trajectory_sim/data` + the thresholds CSV). Two packs
  from the start: `core` (AIP fixes/airways, thresholds, FIR/sectors,
  performance, CAT62) and `procedures` (the AIXM-derived SID/STAR/approach
  layers), so Decision B can be reversed by not bundling the second one.
- `api/datapack.py`: locate, verify, load the active pack; engine reads data
  from the pack path instead of fixed repo paths.
- `desktop/src-tauri/datapack.rs`: manifest check, download, verify, swap.
- `infra/data-worker/` (Cloudflare Worker serving the manifest).

**Phase 3**
- `desktop/src-tauri/` macOS entitlements and notarisation config; mac jobs in
  the workflows; `desktop/sidecar/engine-mac.spec`.

**Phase 4**
- `infra/update-worker/` (R2-backed manifest with rollout percentage and
  kill-switch); Sentry init in shell and sidecar behind the opt-in flag;
  `web/components/SettingsBackend.tsx` for hybrid mode (flag-gated).

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PyInstaller misses GDAL/PROJ data; engine fails on a clean machine | Medium | High | Phase 0 builds and tests on a VM with no Python; explicit `datas` for `proj.db` and GDAL data; a startup self-test (`pyproj.Geod` + one `pyogrio` read) that fails loudly |
| Antivirus quarantines the sidecar | Medium | High (app won't start) | One-folder build, no UPX, signed, vendor submissions, clear error message with "Export logs"; crash report records AV name |
| SmartScreen "unknown publisher" at install **and at every update** (unsigned by decision) | Certain | Medium | Screenshot + wording in the download page, first-run guide and update badge; Microsoft safe-app submission per release; switch Trusted Signing on when it starts costing users |
| deck.gl on WKWebView (macOS) renders badly or slowly | Medium | Medium (mac only) | Phase 3 validation before shipping; `!gpu` fallback; Electron as escape hatch |
| Orphaned sidecar after a crash keeps the port and RAM | Medium | Low | PID file sweep, parent-death exit, random port per launch |
| Update breaks on a user's machine | Low | High | Signed manifests, health check after update, "reinstall previous version", forced-update only for critical fixes |
| Repository made public exposes code and committed data | — (decided) | Low | Review the repo for secrets before flipping (none committed today; `.gitignore` already excludes the AIXM export and local data); the licence note in `.gitignore` stays |
| AIXM-derived procedure layers: provider later objects to redistribution | Medium | High (legal) | Decision B: shipped now, but built as a separate `procedures` data pack so a release can stop bundling it and serve it only to licensed accounts; keep the provider's terms on file |
| Large memory use on 8 GB machines during a traffic-day import | Medium | Medium | Worker count from available RAM as well as cores (min 1.2 GB free per worker); the server-side memory fix from `PERFORMANCE_NOTES.md` Part 4 |
| Version drift between shell, engine and web | Medium | Medium | Single `VERSION` file, CI check, health-check mismatch is fatal |
| macOS notarisation rejects a sidecar `.dylib` | Medium | Medium (mac only) | Sign every binary with hardened runtime; known PyInstaller + notarytool recipe; test in Phase 3 CI |

## 9. Costs

| Item | Cost | When |
|---|---|---|
| Azure Trusted Signing | ~US$10/month — **deferred** (no Windows signing for now) | When revisited |
| Apple Developer Program | US$99/year — required for macOS | Phase 3 |
| GitHub Releases hosting | Free | Phase 1 |
| Cloudflare R2 + Workers | Free tier covers it (10 GB storage, 10 M requests/month) | Phase 2 (data), Phase 4 (updates) |
| Sentry | Free tier (5k errors/month) | Phase 4 |
| Windows test VMs | Free (Windows Sandbox, Hyper-V evaluation images) | Phase 0 |
| Two Apple Silicon test machines | Hardware you may already have; otherwise a used M1 Mac mini ~US$400 | Phase 3 |
| CARTO key with redistribution terms | Quote needed; Esri fallback until then | Optional |
| **Recurring total** | **US$0 until Phase 3; US$99/year from macOS onwards** (+ ~US$120/year if Windows signing is added) | |

## 10. Open questions — all answered (2026-10-02)

A, B and C are recorded in section 0. Nothing blocks Phase 0.

## 11. Inputs from BearCat AEL — received 2026-10-02

| Input | Status | Where |
|---|---|---|
| App icon | Received: 6-size ICO up to 256x256 (Windows maximum). A 1024x1024 master would sharpen the macOS Dock icon on Retina; optional | `desktop/icons/app.ico` |
| Support contact | kruammek@bearcat.co.th | `desktop/brand.json` |
| Publisher identity | BearCat AEL Co; bundle identifier `th.co.bearcat.atcfts` | `desktop/brand.json` |
| EULA | Basic draft with standard terms (licence, restrictions, data disclaimer, no warranty, Thai law). Marked as a placeholder for BearCat to edit before public release | `EULA.md` |
| Privacy text | To be drafted in Phase 1 alongside the opt-in crash-report toggle | `PRIVACY.md` (Phase 1) |
| Visual design | None needed; reuses the web app's components, stylesheet and night-radar console guide | — |

Nothing blocks Phase 0.

## 12. Phase 0 results — spike complete (2026-10-02)

Built and measured on the development PC (Windows 11 Pro 26100, Ryzen 7
9800X3D, RTX 4070, WebView2 154). Toolchain installed for it: Rust 1.99
(rustup), Visual Studio 2022 Build Tools C++ workload (MSVC 14.44),
PyInstaller 6.22.3, Tauri CLI 2.12.1.

### Exit criteria

| Criterion | Result |
|---|---|
| Packaged engine generates the 1,990-flight day with no Python on the machine | **Met in part.** The PyInstaller build ran with a stripped environment (no venv, no `PYTHONPATH`, only `System32` on `PATH`) and from the installed location; all 1,983 flights generated in **23 s** through the desktop window. Not yet run on a separate clean VM — carry to Phase 1's smoke test |
| Map pans at ≥ 60 fps in WebView2 | **Met.** 178.6 fps average, p95 5.6 ms, p99 5.7 ms, worst frame 28 ms, no long tasks, with 1,983 flights loaded — the same as Chrome. WebGL2 available |
| Installer prototype ≤ 110 MB | **Met: 56.1 MB** (NSIS, per-user). 206 MB installed. Silent install took 5 s, no admin prompt, Start-menu entry created |
| No Python or Node needed by the user | **Met.** The installed app started its own engine in 1.5 s and generated a flight |

### Measurements

| Item | Value |
|---|---|
| Engine folder (PyInstaller one-folder) | 193 MB: navdata 42 MB, pandas 18, pyproj 17, pyogrio 13, numpy 7, the rest Python + DLLs |
| Shell executable (front end embedded) | 17.1 MB |
| Engine start, cold | 1–2 s to `/api/health` |
| Memory at rest | shell 43 MB + engine 103 MB (+ WebView2 processes) |
| Memory after a 2,000-flight import | engine + 7 workers = **1,480 MB** |
| One 40-flight chunk, frozen engine | 1.5 s, 7 workers |
| Output, frozen vs venv on the same machine | **Identical** (40 flights, every field) |
| Output, Windows vs Render (Linux) | Differs only in the last float digit of lat/lon/track (1e-15) — maths-library noise, not packaging |
| Shutdown | Closing the window stops the engine and all workers; port released. Killing the engine process alone also takes its workers with it in < 1.5 s |

### What the spike built (committed under `desktop/`)

- `sidecar/engine_main.py` — entry point; `multiprocessing.freeze_support()`
  first, then uvicorn on `ATC_BIND`:`ATC_PORT`.
- `sidecar/engine.spec` — one-folder, UPX off; bundles `web/public/data`,
  `trajectory_sim/data`, the thresholds CSV, and pyogrio's `gdal_data` /
  `proj_data` (pyogrio has no PyInstaller hook).
- `src-tauri/` — Tauri 2 shell: starts the engine hidden, waits for its port,
  then creates the window; logs to `%LOCALAPPDATA%\th.co.bearcat.atcfts\logs`;
  stops the engine on exit. NSIS per-user bundle with the engine as a resource.
- `web/next.config.mjs` — `DESKTOP_BUILD=1` switches on `output: "export"`
  (written to `web/.next-desktop/`); the Vercel build is unchanged.

Build, from the repo root:

```
python -m PyInstaller --noconfirm --clean desktop/sidecar/engine.spec --distpath desktop/sidecar/dist --workpath desktop/sidecar/build
cd web && DESKTOP_BUILD=1 NEXT_DIST_DIR=.next-desktop NEXT_PUBLIC_API_BASE=http://127.0.0.1:8765 npx next build
cd ../desktop && npx tauri build      # needs %USERPROFILE%\.cargo\bin on PATH
```

### Findings that change Phase 1

| Finding | Consequence |
|---|---|
| The static export copies `public/data` (42 MB) and the engine bundles the same folder | De-duplicate: ship it once (the data pack) and have the front end fetch its one static file from the engine, or exclude `data/` from the export. Saves ~40 MB installed |
| `proj.db` is bundled twice (pyproj and pyogrio) | Keep one; point `PROJ_DATA` at it. ~9 MB |
| `api/server.py` writes exports to `_ROOT/api/_outputs`, which is inside the install folder | Move to the user data folder (`ATC_OUT_DIR`) — an install under Program Files would not be writable, and uninstall would delete user exports |
| The spike uses a fixed port (8765) baked into the front end, and reuses whatever is already listening there | Phase 1 as planned: random port, session token, runtime config, PID file |
| The engine's CORS allow-list needed `WEB_ORIGIN=http://tauri.localhost` | Phase 1 drops CORS in local mode instead |
| 1.48 GB after a full import with 7 workers | Size the pool from free RAM as well as cores on 8 GB machines; the server-side memory fix (PERFORMANCE_NOTES Part 4) matters here too |
| Stacked route badges at busy airports overlap into unreadable blocks | Cosmetic, same as the web build; consider collision filtering on the GPU text layer |
| Windows shows the SmartScreen prompt for the unsigned installer | As decided; document it on the download page |

### Gotchas hit

| Problem | Cause | Fix |
|---|---|---|
| `tauri build` failed: "cargo: program not found" | A fresh rustup install is not on the current session's PATH, and a Git Bash `PATH` entry built from `%USERPROFILE%` is not resolved | Prepend `%USERPROFILE%\.cargo\bin` in PowerShell before building |
| Git's `link.exe` is not a linker | Git Bash ships a Unix `link` | Install the VS Build Tools C++ workload; cargo finds MSVC itself |
| Next's static export was not in `out/` | With a custom `distDir`, the export is written there | `frontendDist` points at `web/.next-desktop` |
| PyInstaller warned "Datas for pyproj not found" and errored on `pyogrio.tests.*` hidden imports | Harmless: `proj.db` is collected, and the test modules are excluded on purpose | Narrow `collect_submodules("pyogrio")` to skip tests |
| `tauri icon` does not accept `.ico` | It wants a PNG or SVG master | Extract the 256 px image and upscale to 1024 (`desktop/icons/app-1024.png`) until a real master exists |
| The window could open before the engine answered | Windows declared in `tauri.conf.json` are created before `setup` | Create the window in code after the engine's port is open |

### State left on the development PC

The spike build is **installed** per-user at
`%LOCALAPPDATA%\ATC Fast-Time Simulation Tool` (Start menu → "ATC Fast-Time
Simulation Tool"). Uninstall from Settings → Apps, or run `uninstall.exe` there.

## 13. Phase 1 progress (2026-10-02)

### Done and verified

| Item | How it works | Verified by |
|---|---|---|
| Secure local engine | The shell picks a free port, generates a 32-byte session token, and starts the engine with `ATC_LOCAL_MODE=1`. The engine binds `127.0.0.1`, requires `Authorization: Bearer <token>` on every `/api/*` request except `/api/health` and CORS preflights, accepts `?t=<token>` on GET only (for navigation downloads), allows only the Tauri page origins, and serves no docs | `tests/test_session_token.py` (8 tests); `desktop/smoke.ps1`; from the page: no token 401, wrong token 401, header 200, query 200, docs 404 |
| Runtime backend config | `web/lib/backend.ts`: the shell injects a frozen `window.__APP_CONFIG__` before any page script; the web build falls back to `NEXT_PUBLIC_API_BASE`. Every engine call goes through `apiFetch` | `backend.test.ts` (12 tests, incl. a source scan that fails on a bare `fetch(` in the API modules) |
| One version source | `VERSION` -> `api/version.py` at runtime; `scripts/sync_version.py` stamps and checks the shell manifests; `/api/health` reports it and the shell compares | smoke test "engine version matches VERSION" |
| User data out of the install folder | Exports in `%LOCALAPPDATA%\th.co.bearcat.atcfts\exports` (`ATC_OUT_DIR`), logs in `...\logs`, `engine.pid` beside them | app run; About -> Open exports folder |
| No orphaned engines | (1) the shell stops the engine on exit; (2) the engine watches the shell's PID and exits when it is gone; (3) the next launch sweeps a PID file left by a crash | smoke test "engine exits when the app is killed" |
| Start-up failure is visible | A native error box naming the log file, instead of a silent exit | code path; not yet forced in a test |
| About / updates UI | Info button in the nav bar (desktop only), dot when an update waits; dialog shows version, engine, navdata cycle, update status, install button (held while a replay runs), logs/exports folders, support address | driven through WebView2's debug port; screenshot |
| Updater | `tauri-plugin-updater`; manifests signed with a minisign key (private half outside the repo, public half in `tauri.conf.json`); check on launch + every 4 h + on demand; download and install only on request; the engine is stopped before the installer runs | compiles, build emits `.sig`; `check_update` fails cleanly with no release. **End-to-end update not yet tested** (needs a public release) |
| Release folder | `scripts/make_update_manifest.py` -> `release/ATC-FTS_<v>_x64-setup.exe` + `release/latest.json` | built: 57.3 MB installer, manifest with signature |
| Build + smoke scripts | `desktop/build.ps1`, `desktop/smoke.ps1` | both run locally |
| CI | `.github/workflows/desktop.yml`: tests on every push; on a `v*` tag, build -> smoke -> publish release | not yet run |

### Bugs found in this step

| Problem | Cause | Fix |
|---|---|---|
| Procedure lookups returned 401 in the desktop app | Five `fetch(` calls in `api.ts` were split across lines or took a prebuilt URL, so a one-line search-and-replace to `apiFetch` missed them | Regex replace; a test now scans the modules for any bare `fetch(` |
| `smoke.ps1` would not parse | Windows PowerShell 5.1 reads a BOM-less UTF-8 script as ANSI; an em dash became a quote character | Keep `.ps1` files ASCII-only |
| Plan said "no CORS in local mode" | The page (`http://tauri.localhost`) and the engine (`http://127.0.0.1:<port>`) are different origins | CORS stays, restricted to exactly the shell's page origins, no pattern |
| Updater error text was developer wording | Raw plugin error shown | Plain sentence; detail in the tooltip |

### Remaining for Phase 1

1. **Make the repository public** (decision A) and add the `TAURI_SIGNING_PRIVATE_KEY` secret; then tag `v0.2.0`.
2. End-to-end update test: install 0.2.0, publish 0.2.1, confirm the app finds it, installs it and restarts on it.
3. First-run EULA screen; `PRIVACY.md`; third-party notices.
4. De-duplicate `public/data` (42 MB shipped twice) and `proj.db`.
5. Worker count from free RAM on 8 GB machines.
6. Run the smoke test on a clean Windows 10 and 11 VM.

## 14. Release pipeline and auto-update: verified end to end (2026-10-02)

The repository is public. Releases: `v0.2.0`, `v0.2.1` (and `v0.2.2` with
the update banner), all built and published by `.github/workflows/desktop.yml`.

### CI release run (per tag)

`test` (Linux, ~1.5 min) then `release` (Windows, ~17 min cold): tag matches
`VERSION` -> install toolchains -> `desktop/build.ps1` -> `desktop/smoke.ps1
-Install` on the clean runner (8 checks) -> publish `ATC-FTS_<v>_x64-setup.exe`
and `latest.json`. The smoke test passing on GitHub's runner is the
"clean machine" check Phase 0 could not do locally.

### Update test, 0.2.0 -> 0.2.1

| Step | Observed |
|---|---|
| Public manifest, no login | `releases/latest/download/latest.json` -> 0.2.1 |
| Installed 0.2.0 (CI build), before the release | `check_update` -> not available |
| After `v0.2.1` was published | About: "Version 0.2.1 is available", release notes shown, dot on the nav button |
| Install and restart | Download with live progress (0 -> 100 %), app exited after 10 s, installer ran without prompts |
| Relaunch | The app came back by itself; exe 0.2.1, engine 0.2.1 |
| Feature only in the new version | "What's new in 0.2.1" section present (absent in 0.2.0) |
| Check again | "You are up to date." |

No SmartScreen prompt appeared during the update: the updater downloads the
installer itself, so the file carries no "downloaded from the internet" mark.
(A first install from a browser download still shows the prompt, unsigned.)

### Update notice

0.2.0/0.2.1 only showed an update as a dot on the About button, which is not
on screen until a flight exists. From 0.2.2 a banner appears on every screen
(top-right): "Version X is available" with Install and restart / What's new /
Later. Verified with a local build of the banner code stamped 0.2.0 against
the published 0.2.1. The check runs 3 s after launch and hourly.

### Bugs and gotchas in this step

| Problem | Cause | Fix |
|---|---|---|
| CI failed on `planScan.test.ts` (1341 ms > 1000 ms) | A timing guard tuned for a desktop; shared runners are ~4x slower | 5 s budget when `CI` is set |
| First banner covered the opening card's title and Generate button | Top-centre placement | Top-right compact card |
| `release/` kept the previous installer | Folder never cleaned | The manifest script removes stale installers |
| To test the banner without two more release cycles | Only a version that contains the banner can show it | Build the new code stamped with an older version locally; restore `VERSION` afterwards |

### Operational notes

- **Releasing:** edit `VERSION` and `RELEASE_NOTES.md`, run
  `python scripts/sync_version.py`, commit, `git tag v<version>`, push the tag.
- **The updater key** (`%USERPROFILE%\.tauri\atc-fts-updater.key`, also the
  `TAURI_SIGNING_PRIVATE_KEY` secret) must be backed up. Without it no
  installed app can ever be updated again.
- Every push to `main` also redeploys the hosted API (Render) and site (Vercel).

### Second update, 0.2.1 -> 0.2.2 (2026-10-02, next morning)

The same test, run on the copy that had updated itself the night before:
0.2.1 offered 0.2.2 with its notes; download 0 -> 97 % in 7 s; the old app
exited; the new one was up 7 s later as 0.2.2 (app and engine), showing
"What's new in 0.2.2" and "You are up to date". Two consecutive real updates
is Phase 1's exit criterion.

### Installer licence page

Confirmed without clicking through an install: the generated NSIS script
(`desktop/src-tauri/target/release/nsis/x64/installer.nsi`) defines `LICENSE`
and inserts `MUI_PAGE_LICENSE`. Silent installs and updates skip the page.

## 15. Phase 1 close-out and the bridge to Phase 2 (0.2.3)

| Item | What was done | Verified |
|---|---|---|
| Static data shipped once | The desktop front end no longer contains `data/`. Every data module uses `dataFetch` (`web/lib/backend.ts`): a plain fetch on the web, a request to the engine with the session token on desktop. The engine mounts its bundled `web/public/data` at `/data` in local mode, behind the token gate | Guard test scans the 8 data modules for a bare `fetch(`; token test covers `/data/`; in-window test below |
| Third-party notices | `scripts/gen_notices.py` lists the engine's Python packages (from installed metadata), the front end's production packages (from `package-lock.json`) and the shell's crates (`cargo metadata`), plus notes for PyInstaller's bootloader, GDAL, PROJ, GEOS, WebView2, tiles and navdata | 24 Python, 157 JS, 477 Rust components |
| Licences reachable in the app | EULA and notices are bundled as resources (`licenses/`); About has "Licence agreement" and "Third-party licences", which open them in the default viewer | Files present in the build output |

This is also the first half of Phase 2: the front end now gets **all** static
data from one place in the engine, so a data pack only has to change what
that one folder is.

### Two bugs found while testing 0.2.3 in the window

**1. The first procedure lookup after launch answered 404.**

- *Symptom.* In the app, `GET /api/procedures/VTSP/URGA1D?type=STAR&...`
  returned 404 once, right after an import. The same request to a standalone
  engine (frozen or from source, local mode or not) and to the hosted API
  returned 200.
- *Cause.* A race in `trajectory_sim/navdata.py::_load_procedures`. The
  SID/STAR/approach index is built lazily on first use, and the "loaded" flag
  was set **before** the index was built. The API answers on a thread pool;
  the page asks for an airport's procedure list and a procedure's legs at the
  same moment; the second request saw "loaded", searched a half-built index
  (SIDs read, STARs not yet) and reported "not found". It is an old bug: the
  hosted API has it too, but only for the first two requests after a cold
  start, so nobody saw it. A desktop app cold-starts its engine at every
  launch, which makes it visible every time.
- *Fix.* Build the index under a `threading.Lock` with a second check inside
  the lock, and set the flag only **after** indexing and sorting. A request
  arriving meanwhile waits for the complete index.
- *Test.* `tests/test_navdata_concurrency.py`: (a) a second lookup that
  starts while the first is still reading sources, with the read slowed to
  widen the window; (b) eight simultaneous first lookups. Against the old
  code: 7 of 8 fail with "Procedure 'URGA1D' not found at 'VTSP'". With the
  fix: all pass.

**2. The app could stay running with no window.**

- *Symptom.* After a test, asking the app to close from PowerShell
  (`$process.CloseMainWindow()`) did nothing; a minute later the shell and 8
  engine processes were still there. It had worked on every earlier run.
- *Investigation.* Not reproducible at first (three clean closes in ~0.3 s:
  plain, with WebView2 debugging, after an import with 8 engine processes).
  The transcript showed the failing run differed in one thing: the window was
  not in front. Listing the process's top-level windows showed two visible,
  unowned ones: `Tauri Window` (the real one) and a 16x16
  `Tao Thread Event Target` (the windowing library's internal message
  window). .NET's `MainWindowHandle` is "the first such window in stacking
  order", so with the app minimised it was the internal one.
- *Cause, confirmed one variable at a time on fresh instances:*

  | Close sent to | Result |
  |---|---|
  | the real window (what clicking X does) | clean exit in ~0.3 s |
  | `taskkill /PID` without `/F` | clean exit |
  | the internal window only | it is destroyed; the app keeps running |
  | the internal window, then the real one | **windowless process that never exits, engine still running** |

  Destroying the internal window breaks the event loop's ability to finish an
  exit. Clicking X never does this, but any tool that picks a process's "main
  window" by stacking order can.
- *Fix, two parts.*
  1. Shell (`exit_when_window_is_gone` in `desktop/src-tauri/src/lib.rs`,
     Windows only): a thread outside the event loop polls the real window
     (`IsWindow` and still owned by this process, because handle values are
     reused). If it has been gone for 3 s and the process is still alive, it
     stops the engine and exits. A normal exit takes ~0.3 s and never reaches
     it.
  2. Test tooling: never use `CloseMainWindow()` on a Tauri app. Find the
     window by class (`Tauri Window`) and post `WM_CLOSE` to it.
- *Test.* `desktop/smoke.ps1` now launches the app three times: a normal
  close must end the app and the engine; a close that hits the internal
  window first must still end both; a hard kill must take the engine with it.
  The second check failed on the build without the guard (still running
  after 15 s).

**Verified on the rebuilt 0.2.3:** smoke test 11/11 (normal close 285 ms; the guard ended a windowless app in 3.6 s and logged it). In-window test on a cold engine, three launches: no failed requests (the 404 appeared in 3 of 3 launches before the fix), 30-31 data files served by the engine, 401 without the token, traversal 404, 54 airport markers, 520 flights imported and generated. Python: `tests` 31 passed, `trajectory_sim/tests` 278 passed; front end 957 passed; type check clean.

### 0.2.3 released; third real update, 0.2.2 -> 0.2.3

CI run for `v0.2.3`: tests green on Linux (including the new race test);
Windows job built, installed and smoke-tested 12/12 on the clean runner
(the two new window checks included), then published. On the development PC
the installed 0.2.2 showed "Version 0.2.3 is available" by itself 2 s after
the debugger attached, downloaded 0 -> 100 %, closed after 12 s, and came
back on its own as 0.2.3 (app and engine): licence buttons present, 54
airport markers from engine-served data, "You are up to date", no failed
requests.

One thing to watch: on the runner "closing the window ends the app" took
3.3 s against 0.27 s locally. That is the watcher's 3 s grace plus one poll,
so on that machine the normal exit may not have completed by itself and the
watcher finished it. Harmless (that is what it is for), but if a user PC
shows the same, the normal exit path deserves a look.

## 16. Phase 2: navigation-data packs (0.3.0)

**Goal met locally:** new navigation data reaches an installed app without a
new installer; a damaged, forged, too-new or broken pack never gets used and
never stops the app.

### How it works

```
publisher                         installed app
---------                         -------------
web/public/data  --build_data_pack.py-->  atc-data_<v>.zip  + data-manifest.json
   (+ pack.json)        signs the zip          |                    |
                                               v                    v
                              GitHub release tagged "data" (never "latest")
                                                                    |
shell, 8 s after launch and every 6 h:  GET data-manifest.json  <---+
  newer than the data in use? app new enough?  -> GET the zip
  size == manifest, SHA-256 == manifest, minisign signature valid for the
  app's built-in public key  -> unzip to data-packs/<v>.partial -> rename to
  data-packs/<v> -> write data-packs/current.json -> tell the page
page: banner "New navigation data is ready" [Restart now] [Later]
next start: shell passes data-packs/<v> to the engine as $ATC_DATA_DIR
engine: uses it if it is a whole data tree, schema 1, and NEWER than its
  bundled data; otherwise uses the bundled data and reports why
```

| Piece | Where | Notes |
|---|---|---|
| The data tree and its version | `web/public/data/pack.json` (`schema`, `version` = AIRAC date + build, `airac`, `min_app`) | The bundled tree describes itself the same way a pack does |
| Engine: which tree to read | `trajectory_sim/datapaths.py` (`resolve`, `active`, `data_dir`) | The only two readers were `api/server.py::_DATA` and `airspace.py::_SECTORS_DIR`; both now ask this module. Decided once per process; workers inherit the environment |
| Engine: report it | `/api/health`: `data_version`, `data_source` (`bundled` / `pack`), `data_pack_rejected` | The shell learns the outcome from here instead of re-implementing the rules |
| Build + sign a pack | `scripts/build_data_pack.py` | Reproducible zip (sorted entries, fixed timestamps); signed with `tauri signer sign` and the updater key; signature goes in the manifest |
| Shell | `desktop/src-tauri/src/datapack.rs` | Check, download, verify, unpack (no path may leave the folder; size caps), select by rename, prune to the newest two |
| Shell wiring | `lib.rs`: `start_engine(.., pack)`, retry without the pack if the engine fails on it, deselect a rejected pack, commands `data_status` / `check_data_update` / `restart_app`, event `data-status` | |
| Page | `web/lib/desktop.ts` (`useDesktopData`), `components/desktop/DataBanner.tsx`, data block in `AboutDialog.tsx` | The page only mirrors the shell's state |
| Publish | `.github/workflows/data.yml`: tag `data-<version>` or manual run | Pack uploaded before the manifest; release `data` created with `--latest=false` |

### Decisions and deviations from sections 3.3 and 7

- **Host: a GitHub release tagged `data`**, not a Cloudflare Worker. No new
  infrastructure; the URL is in one constant (`MANIFEST_URL`) and can move.
  The Worker is still the plan when accounts arrive (Phase 4).
- **One pack, not two** (`core` + `procedures`). The pack is the whole
  `web/public/data` tree. Splitting it is a build-script change if the AIXM
  procedures ever have to be withheld.
- **Not in the pack yet:** aircraft performance tables and the CAT62
  reference (`trajectory_sim/data`) stay in the program. The threshold
  elevation table is read from the pack first if a pack carries one.
- **Trust model = the updater's.** The manifest is not signed and not
  trusted; the signature covers the pack and is checked against the public
  key compiled into the app (read from the updater's config, so there is one
  key). `ATC_DATA_MANIFEST_URL` can point the app elsewhere for tests; that is
  safe because the signature check still applies.
- **Applied at the next start, never live.** A running simulation keeps its
  data.
- **The newer of pack and bundled wins**, decided by the engine, so a program
  update with newer data supersedes an older downloaded pack.
- **Token header plumbed, unused:** if `data-packs/token.txt` exists its
  content is sent as a bearer token with the manifest request. Nothing
  creates that file.
- No new Rust packages: `reqwest`, `rustls`, `minisign-verify`, `sha2`,
  `base64`, `zip` were already in the tree through the updater plugin
  (`Cargo.lock` gained 7 dependency lines, no new entries).

### Verified (local build of 0.3.0)

`desktop/datapack_e2e.py` - real packs, really signed, served from a local
web server, against the built app, 13/13:

| Case | Result |
|---|---|
| App starts on bundled data | `2026.09.03.1 (bundled)` |
| Newer signed pack | downloaded, verified, selected 8 s after launch (9.2 MB) |
| Running engine | not switched |
| After restart | engine on `2026.09.03.2 (pack)`; not downloaded again |
| One byte of the zip flipped | refused: "SHA-256 does not match" |
| Manifest rewritten to match the altered zip | refused: "signature does not match" |
| Signature removed | refused: "not signed" |
| `min_app` 99.0.0 | left alone, logged |
| Version not newer | left alone |
| Selected pack broken on disk | app starts on bundled data; pack deselected |

Unit tests: `tests/test_datapaths.py` (7: fallbacks, older-than-bundled,
version ordering, a fresh engine process on a pack) and 5 Rust tests in
`datapack.rs` (version ordering, folder-name safety, size/hash/no-signature
refusal, zip path traversal, pruning).

In the window (WebView2 + CDP): banner appeared by itself 7 s after launch
("New navigation data is ready - AIRAC 2026-09-03 (2026.09.03.2)"); "Restart
now" restarted the app; after it `/data/pack.json` served to the page was the
pack's; 54 airport markers; flights generated; About showed "Navigation data
2026.09.03.2 (a downloaded update). It is up to date."; no failed requests.
Smoke test 11/11 on the same build.

### Bug found in this step

"What's new" in a locally built app showed a garbled dash. Cause:
`build.ps1` read `RELEASE_NOTES.md` with `Get-Content -Raw`, which in Windows
PowerShell 5.1 decodes a BOM-less UTF-8 file as ANSI. CI was unaffected
(PowerShell 7). Fix: `-Encoding UTF8`.

### Verified in public (2026-10-02)

1. **Release 0.3.0.** CI on the clean Windows runner: shell unit tests 5/5,
   smoke test 12/12, data-pack end-to-end test 13/13, then published. (Normal
   window close took 2.0 s there against 0.27 s on the development PC: a slow
   machine, under the watcher's 3 s, so the exit completed by itself. The
   smoke test now says so explicitly if the watcher had to finish a normal
   close.)
2. **Fourth real app update, 0.2.3 -> 0.3.0.** Banner by itself, download
   0 -> 100 %, closed after 7 s, back on its own as 0.3.0. With no data pack
   published yet, About said "Could not check for new navigation data" (the
   manifest URL answered 404) and the app ran on its bundled data.
3. **First data pack published** with the workflow
   (`gh workflow run data.yml -f version=2026.09.03.2 -f notes="..."`, 1
   minute): `atc-data_2026.09.03.2.zip` (9.6 MB) and `data-manifest.json` on
   the release tagged `data`; "latest" stayed `v0.3.0`. The manifest URL
   answered 404 for a few seconds after the upload, then 200.
4. **The installed 0.3.0 took it without an app update.** About > "Check for
   new data": downloaded and verified in 1.8 s, "Navigation data 2026.09.03.2
   has been downloaded" with the pack's notes; "Restart now" restarted the
   app; engine then on `2026.09.03.2 (pack)`, the page served the pack's
   `pack.json`, flights generated, 54 airport markers, "It is up to date", no
   failed requests; the installed program still 0.3.0.

Phase 2's exit criteria are met: a pack reaches an installed app without an
app update; corrupted packs are rejected; the token header is plumbed and
unused.

## 17. Phase 4a: hardening that needs no new accounts (0.4.0, 0.4.1)

macOS (Phase 3) is blocked on an Apple Developer account, so Phase 4 was
started first, with the parts that need no new service.

### What was built

| Item | Where | How it works |
|---|---|---|
| Staged rollout | `desktop/src-tauri/src/release.rs`, `latest.json` field `rollout` (0-100) | Each installation draws a number 0-99 once (`install-bucket` in the data folder). The background check offers an update only when that number is below `rollout`. "Check for updates" in About is never held back. Raising the figure only adds installations |
| Forced update | `latest.json` field `min_supported`; `web/components/desktop/RequiredUpdate.tsx` | A version below the minimum gets a screen that cannot be dismissed, with one button: Install and restart. Overrides the rollout. For critical fixes only |
| Halt a bad release | `scripts/release_control.py halt` | Marks the previous release "latest" and the bad one a pre-release: nobody is offered it any more. `resume --tag` undoes it. No downgrade for those who already have it: follow with a fixed release |
| Change policy after publishing | `scripts/release_control.py rollout N`, `require X`, `status` | Downloads `latest.json` from the release, edits it, uploads it back. Possible because the manifest is not signed (the installer is) |
| Policy at build time | `desktop/release.json`, read by `scripts/make_update_manifest.py` | Validated: rollout 0-100; `min_supported` may not be newer than the release itself |
| Engine supervision | `watch_engine` in `lib.rs` | A thread checks the engine process every second. If it has exited and was not stopped on purpose: log, event `engine-down`, native dialog "Restart now / Not now" |
| Diagnostics export | `desktop/src-tauri/src/diagnostics.rs`, About > Export diagnostics | `diagnostics_<UTC time>.zip` in the exports folder: summary (versions, Windows version, WebView2 version, processors, update group, engine health, data status) and the last 2 MB of each log. The profile folder is rewritten to `%USERPROFILE%`. Nothing is sent |
| Logs | `lib.rs` | Shell log rotates at 5 MB, keeps 3. `engine.log` of the previous run is kept as `engine.previous.log` (it is the one that explains a crash) |

### Decisions

- **Rollout and minimum version live in the manifest, decided by the app**,
  not by a server choosing per request. It needs no server, and the
  installation's number never leaves the machine. The Cloudflare Worker of
  section 3.1 would give exact percentages and statistics; this gives the
  control without the infrastructure. The fields are not a security
  boundary: a forged manifest can only offer or insist on an update that
  must still carry our signature and be newer.
- **A manual check ignores the rollout.** Someone asking for the update gets
  it; after that the background check asks the same way, so the update they
  were shown does not vanish an hour later.
- **Old apps ignore the new fields** (0.3.0 and earlier update as before),
  so the first release carrying them needs no special handling.
- **A dead engine is reported, not silently replaced.** The engine holds the
  session's generated flights in memory; restarting it behind the user's
  back would leave a window full of flights the engine no longer knows.
- **Not done, needs an account or a decision:** opt-in crash reporting
  (Sentry), the R2 + Worker update host, "reinstall previous version",
  hybrid mode, antivirus false-positive submissions, a Windows 10 test.

### Verified

**Automated.** 13 shell unit tests (release policy 5, diagnostics 3, data
packs 5). Smoke test, now 15 checks over five launches. Data-pack end-to-end
test 13/13. All three run on the clean Windows runner before a release is
published; v0.4.0 and v0.4.1 both passed.

**Release control against the live release.** A copy of the 0.4.0 code
stamped 0.3.9 was run against the published v0.4.0 while the policy was
changed with `scripts/release_control.py`. This installation's number is 79.

| Live manifest | Background check | Asked by hand | On screen |
|---|---|---|---|
| rollout 100 | offered | offered | "Version 0.4.0 is available" banner |
| rollout 79 (just outside) | held back | offered | no update banner |
| rollout 80 (just inside) | offered | offered | banner |
| rollout 0, `min_supported` 0.4.0 | offered, **required** | required | "Update required" screen; Escape and clicking outside do nothing |
| `require 0.5.0` on v0.4.0 | refused by the script: nobody could reach it | | |
| `halt` | no update (served v0.3.0 again; v0.4.0 marked pre-release) | no update | nothing |
| `resume --tag v0.4.0` | offered | offered | banner |

**How fast a change reaches apps (measured).** GitHub serves release assets
through a cache.

- Polling the public URL: the new manifest first appeared 66-123 s after
  the change (8 changes).
- An app checking every 3 s saw one clean switch, 72 s and 10 s after two
  changes (93 checks each, no flapping).
- An app whose previous check was about two minutes earlier got the *old*
  manifest once, and the new one 4 s later (seen four times): a cache
  serving its stale copy once while it refreshes.
- An app that had not checked for 4 minutes, and for 10 minutes, got the new
  manifest on its first check.

So a rollout change, a required version or a halt is in force for every app
within about two to five minutes of the command, at that app's next check
(3 s after launch, then hourly). `release_control.py` now waits until the
public URL serves the change and prints how long it took.

**Fifth real update, 0.3.0 -> 0.4.0** on the installed copy: banner after
2 s, download, closed after 9 s, back by itself as 0.4.0. The 0.3.0 app
ignored the new manifest fields, as intended.

**Diagnostics export** in the installed 0.4.0: `diagnostics_<time>.zip`,
4 KB, with `summary.txt` and three logs; 0 files contained the profile path.

**Bug found by the smoke test: a silent hang when the engine cannot start.**

- *Symptom.* The new "app notices when its engine dies" check failed on the
  0.4.1 build though it had passed on 0.4.0.
- *Investigation.* Reproduced by hand three times out of three: the test
  killed the engine the moment its port opened, i.e. while the shell was
  still starting it. The shell logged "The simulation engine stopped while
  starting" - and then stayed alive with no window and no error box.
- *Cause.* `fatal()` showed its box with the dialog plugin from a helper
  thread and waited for it. The plugin queues the box on the app's event
  loop (`run_on_main_thread`), which does not run until the setup hook
  returns - and the setup hook was the one waiting. A deadlock, in every
  release up to 0.4.0: any engine start failure (files quarantined by an
  antivirus, a crash at import) left an invisible process.
- *Fix.* The box is drawn directly with `rfd::MessageDialog` on the setup
  thread (its own message loop, no event loop needed), then
  `std::process::exit(1)`. `rfd` was already in the tree.
- *Test.* `ATC_SELFTEST_FAIL_START=1` makes the engine exit at once; the
  smoke test starts the app with it and checks that a box appears and that
  the app exits when OK is pressed. The engine-died check now waits for the
  window first, so it tests what it names, and also checks a box appears.
  The box's text was read back through UI Automation: the reason, the path
  of `engine.log`, and what to do.

**An update banner that did not appear, once.** On the first launch of the
0.3.9 test copy the page's first update check left no trace in the log and
no banner; six later launches showed the banner 3.6-4.1 s after start. Not
reproduced and not explained. Handling added in 0.4.1: a failed check is
logged with its reason, and a failed background check is retried after 30 s,
2 min and 10 min instead of an hour later (the data check likewise, after
5 minutes, three times).

**Slow exit on the runner.** A normal close took 2.0-3.3 s on the CI runner
(0.27 s on the development PC). The window watcher's grace was 3 s, close
enough to cut a slow normal exit short; it is now 6 s.

**A real staged rollout, 0.4.1 on the installed 0.4.0.** v0.4.1 was
published with `desktop/release.json` at `rollout: 0`.

1. Installed 0.4.0 (number 79), release at 0 %: no banner; background check
   "held back"; "Check for updates" by hand offered 0.4.1.
2. `python scripts/release_control.py rollout 100`: "apps are being served
   the change after 114 s".
3. The first page check 20 s later still got the cache's stale copy (no
   update banner yet); the next launch showed "Version 0.4.1 is available"
   2 s after start.
4. Install: download, closed after 9 s, back by itself as 0.4.1 (sixth
   consecutive real self-update), "up to date", and running on the
   downloaded data pack `2026.09.03.2`.

`desktop/release.json` is back at `rollout: 100` for the next release.

## 18. Where work stands

**Done and verified in public:** Phases 0, 1, 2 and the account-free part of
Phase 4. Releases v0.2.0 to v0.4.1; six consecutive real self-updates on
the development PC, which now runs the installed 0.4.1 on data pack
`2026.09.03.2`.

**Blocked on the owner:** Phase 3 (macOS) needs an Apple Developer account
(US$99/year) and a Mac or macOS runner. Crash reporting needs a Sentry
account; the Worker update host needs a Cloudflare account.

**Loose ends**

- `.github/workflows/keepalive.yml` pings `trajectory-api-zf51.onrender.com`,
  an older Render service, not the one deployed this week.
- The hosted API still cannot hold a 2,000-flight import in 512 MB.
- Back up `%USERPROFILE%\.tauri\atc-fts-updater.key`. It now signs data
  packs as well as app updates.
- `trajectory_sim/tests/test_constraints.py::test_descent_floor_does_not_pin_climb_start`
  fails when `tests/` and `trajectory_sim/tests/` run in one pytest process
  (a threshold altitude of 1036 ft where the test expects under 1000), and
  passes when either folder runs alone. It fails the same way on the commit
  before this work, so it is a test-isolation problem, not a regression; CI
  runs `tests/` only. Four more engine test files need `httpx`, which is not
  in `requirements.txt`.
- The web (hosted) build does not show a data version anywhere; only the
  desktop About does.
