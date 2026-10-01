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
