# Replication playbook

**What this is.** This repository is the *test version* of the ATC Fast-Time
Simulation Tool. Everything done to it between 2026-09-27 and 2026-10-02 —
performance work, hosting, and turning it into an installable desktop app
with auto-update — will be done again on the *complete version*. This file is
the procedure to follow there, in order, with the commands, the checks that
prove each step worked, and every trap that cost time here.

**Standing rule (from the project owner):** whenever a solution works, write
down the whole procedure — what was measured, the cause, the fix, the
numbers, the bugs hit — before moving on. Keep this file and the two below
up to date as part of the work, not after it.

| Document | Holds |
|---|---|
| **REPLICATION_PLAYBOOK.md** (this file) | The procedure to redo everything, step by step |
| [PERFORMANCE_NOTES.md](PERFORMANCE_NOTES.md) | Performance work in full: measurements, root causes, fixes, reusable code (Parts 1–4) |
| [DESKTOP_RELEASE_PLAN.md](DESKTOP_RELEASE_PLAN.md) | Desktop app: architecture decision, roadmap, risks, costs, and measured results per phase (sections 12–14) |

No secret values are recorded in any of them — tokens and keys are named,
never written.

---

## 0. The order that worked

1. **Make it fast locally** (Part A). Measure first; fix the front end's
   drawing, then the server's generation, then the stalls after import.
2. **Put it in Git and host it** (Part B). GitHub → Render (API) → Vercel
   (site). Measure the hosted API with a real request, not a health check.
3. **Decide the desktop architecture on paper** (Part C.0), get the product
   decisions answered, and only then write code.
4. **Spike** the three unknowns before building anything real: the frozen
   engine, the map in the desktop WebView, the installer size (Part C.1).
5. **Build the real app**: secure engine connection, runtime config, one
   version source, updater, update UI (Part C.2–C.6).
6. **Automate the release** and prove the update end to end with two real
   versions (Part D).

What I would do differently next time is in Part F.

---

## Part A — Performance (summary; full detail in PERFORMANCE_NOTES.md)

| Symptom | Cause found | Fix | Result |
|---|---|---|---|
| 500 flights replay at 9 fps | Static route lines repainted every frame on a shared canvas; plane icons rebuilt every frame | Separate canvases/panes; build icons once; then draw traffic on the GPU with deck.gl | 9 → 177 fps (500 flights), 16 → 116 fps (1,990) |
| Parts of the map missing while dragging | The GPU overlay re-synced only when a drag ended | Own Leaflet↔deck.gl overlay, synced every frame | Complete to the screen edge |
| Only ~60 % of the screen drawn | Leaflet's cached size went stale when a side panel resized the map | `ResizeObserver` → `invalidateSize` | Fixed |
| "Generate all" takes 3 minutes for 1,990 flights | Server built flights one at a time on one core; repeated identical maths | Process pool on the server; memoise the unshaped speed lookup; 3 chunk requests in flight | 187.8 s → 72 s (23 s with nothing else running) |
| Tab freezes ~10 s after an import | O(n²) conflict scan on the main thread, run 3× | Web Worker with typed-array inputs; key the config on the one polygon that matters | No freeze; scan 3.3 s off-thread |
| Stutter when panning/zooming with 2,000 flights | One DOM marker per flight = 2,000 compositor layers | Draw the badges on the GPU (deck.gl `TextLayer`) | 121 → 179 fps, 0 stalls |

**The method, every time** (PERFORMANCE_NOTES Part 1 §6, Part 3 §7):

1. Build a realistic heavy data set first (here: 520 and 1,990 flights).
2. Take numbers **without** a profiler (frame times, long tasks); profile
   only to attribute. A profiler invents stalls.
3. If JavaScript looks cheap but frames are slow, take a Chrome trace and
   count composited layers — every per-item DOM element with a 3D transform
   is a layer.
4. Anything O(n²) over the data goes to a worker; anything per-frame leaves
   React; anything static gets its own canvas or goes to the GPU.
5. Prove outputs are unchanged (hash every result before and after).
6. Benchmark before and after under the same conditions, and say what else
   was running.

Pin **deck.gl at exactly 9.3.11** (9.4.0 crashed the WebGL context).

---

## Part B — Git, GitHub, hosting

### B.1 GitHub

```
winget install --id GitHub.cli -e --silent      # if gh is missing
gh auth login --git-protocol https --web        # browser device code
gh auth refresh -h github.com -s workflow       # needed to push .github/workflows
git init && git add -A && git commit -m "..." && git branch -M main
gh repo create <name> --private --source=. --remote=origin --push
```

- Check `.gitignore` first (venv, `node_modules`, build outputs, licensed
  source data). Look for files over 100 MB before the first push.
- **Before making a repo public:** scan tracked files for tokens, keys,
  `.env` files and personal paths (`git grep -nIE "ghp_|sk-|BEGIN .*PRIVATE KEY"`),
  and re-read any licence note about committed data.
- Git identity: `git config user.name / user.email` in the repo
  (a `…@users.noreply.github.com` address works).

### B.2 Render (the Python API)

1. New → **Web Service** (or Blueprint, which reads `render.yaml`) from the
   repo. Root directory = repo root. Build `pip install -r requirements.txt`,
   start `uvicorn api.server:app --host 0.0.0.0 --port $PORT`, health check
   `/api/health`, env `PYTHON_VERSION=3.11.9`.
2. A service created by hand does **not** read `render.yaml`; set its
   environment variables in the dashboard (or the API).
3. Verify with a **real** request, not `/api/health`: send the same 40-flight
   batch the browser sends and time it twice.
4. Read the host's own view through the Render API: plan, region, env vars,
   memory/CPU metrics, and **events** (an out-of-memory kill shows as
   `server_failed … oomKilled`).

Measured on the free plan (512 MB, Singapore): 1.3 s per flight with one
worker, 2.6× faster with two, and an import of ~1,000+ flights is killed for
memory (the server keeps ~0.37 MB per flight for downloads). **A traffic-day
import does not fit the free plan** — that is why the desktop app exists.

### B.3 Vercel (the web front end)

Through the REST API with a **full-scope** token (a limited token can read
but answers `forbidden` to every write):

1. `POST /v11/projects?teamId=…` with `framework: nextjs`,
   `rootDirectory: web`, `gitRepository: {type: github, repo: owner/name}`.
2. `POST /v10/projects/<id>/env` → `NEXT_PUBLIC_API_BASE` = the Render URL,
   for production, preview and development. It is inlined at **build** time:
   set it before the first build.
3. `POST /v13/deployments` with `gitSource` → poll until `READY`.
4. Verify: the site returns 200, the Render URL is inside the built page
   bundle, and the API's CORS preflight allows the site's origin.

---

## Part C — The desktop app

### C.0 Decisions to get answered before any code

Product name; publisher name (bind to one config file so it can change);
update channels; local-only or hybrid backend; how data will be licensed
later; which macOS architectures; telemetry; minimum hardware; where
releases are hosted (a private repo cannot serve updates); whether any
bundled data may be redistributed; whether to code-sign now.

Answers given here: see DESKTOP_RELEASE_PLAN.md §0. In short — Tauri 2 shell,
PyInstaller engine sidecar, Next.js static export, Windows first, stable
channel only, unsigned for now, public GitHub Releases, BearCat AEL Co.

### C.1 Toolchain (Windows)

```
winget install --id Rustlang.Rustup -e --silent
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --silent --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
<venv>\python -m pip install pyinstaller pillow
cd desktop && npm init -y && npm i -D @tauri-apps/cli@^2 && npm i @tauri-apps/api@^2
```

WebView2 ships with Windows 11 (check
`HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-…}`).
After installing Rust, a running shell does not have it on `PATH` —
prepend `%USERPROFILE%\.cargo\bin`.

### C.2 Spike: prove the three unknowns (Phase 0)

| Unknown | How to prove it | Result here |
|---|---|---|
| The Python engine runs frozen, with its worker pool and GDAL/PROJ data | PyInstaller **one-folder** build; run it with a stripped environment (no venv, only `System32` on `PATH`); send a real batch; compare the output with the venv's | Identical output; 7 workers; starts in 1 s; 193 MB |
| The GPU map runs well in the desktop WebView | Launch the shell with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`, connect Playwright over CDP, run the same pan benchmark | 178.6 fps, same as Chrome |
| Installer size | `tauri build` | 56 MB (target was ≤ 90) |

Engine packaging rules that mattered:

- `multiprocessing.freeze_support()` must be the **first** line of the entry
  point, or the worker pool starts more servers instead of workers.
- The engine finds its data relative to its own source files; mirror the
  repo layout inside `_internal` (`web/public/data`, `trajectory_sim/data`,
  root CSVs, `VERSION`) and nothing in the engine has to change.
- `pyogrio` has no PyInstaller hook: add
  `collect_data_files("pyogrio", includes=["gdal_data/**", "proj_data/**"])`.
- UPX off (antivirus), one-folder not one-file (start-up time, notarisation).

Front end: `output: "export"` behind an env switch (`DESKTOP_BUILD=1`).
Check first that the app has no API routes, no server-only features and no
`next/image`. With a custom `distDir` the export is written **there**, not
to `out/`.

Shell (Tauri 2): create the window **in code after** the engine answers —
windows declared in `tauri.conf.json` are created before `setup` runs and
would load before the engine exists. `tauri icon` wants a PNG/SVG, not an
`.ico`.

### C.3 Secure connection between the page and the engine

| Piece | Rule |
|---|---|
| Port | Picked free at launch (`bind 127.0.0.1:0`), never fixed |
| Bind | `127.0.0.1` only |
| Token | 32 random bytes per session; required as `Authorization: Bearer` on every API route except health and CORS preflights; `?t=` accepted on **GET only**, for downloads started by navigation |
| CORS | Still needed (the page and the engine are different origins); allow exactly the shell's page origins, no pattern |
| Docs | Off in local mode |
| Middleware | Pure ASGI, added **before** CORS so it sits inside it (a 401 keeps its CORS headers) |
| User data | Exports and logs under the per-user data folder, never the install folder |
| Engine lifetime | Three layers: the shell stops it on exit; the engine watches the shell's PID and exits when it is gone; the next launch sweeps a PID file left by a crash |
| Start-up failure | A native error box naming the log file |

Front end: one module (`lib/backend.ts`) resolves the engine address and
token — injected by the shell as a frozen `window.__APP_CONFIG__` before any
page script, or the build-time env var on the web. **Every** engine call
goes through one `apiFetch`; add a test that scans the API modules for a
bare `fetch(` — a search-and-replace missed five multi-line calls here.

Verify from outside the app (health 200, API 401, docs 404) and from inside
the page (no token 401, wrong token 401, header 200, query 200).

### C.4 One version source

A `VERSION` file at the repo root. The engine reads it at runtime and
reports it in `/api/health`; a script stamps it into the shell's manifests
and `--check` fails CI if they disagree; the shell compares its version with
the engine's at start-up.

### C.5 Auto-update

1. `npx tauri signer generate --ci -w <path outside the repo>` → key pair.
   Public key into `tauri.conf.json` (`plugins.updater.pubkey`); private key
   into the CI secret `TAURI_SIGNING_PRIVATE_KEY`. **Back the private key
   up** — without it no installed app can be updated again.
2. `bundle.createUpdaterArtifacts: true`; endpoint
   `https://github.com/<owner>/<repo>/releases/latest/download/latest.json`.
3. Shell commands for the page: `check_update` (report only),
   `install_update` (download with progress events, **stop the engine in
   `on_before_exit`** or its files stay locked, then install).
4. A script assembles the release folder: the installer under a name with no
   spaces (GitHub rewrites spaces in asset names) and `latest.json` with the
   signature and the exact asset URL.
5. UI: a banner on **every** screen (not only a dot on a button that may not
   be visible), an About dialog with versions and release notes, install
   held back while a replay runs. Check a few seconds after launch and
   hourly.

### C.6 Build, smoke test, CI

- `desktop/build.ps1`: version check → licence text → freeze engine → static
  export → `tauri build` (signed updater artifacts) → release folder.
- `desktop/smoke.ps1`: start the app; engine started, loopback only, health,
  version match, local mode, 401 without token, docs off, engine dies when
  the app is killed. `-Install` runs the installer first.
- `.github/workflows/desktop.yml`: tests on every push (Linux); on a `v*`
  tag: build on Windows → smoke test on the clean runner → publish.
- Keep `.ps1` files **ASCII-only** (Windows PowerShell 5.1 reads BOM-less
  UTF-8 as ANSI and an em dash becomes a quote).
- A timing assertion tuned on a desktop fails on a shared runner (~4×
  slower): give CI its own budget.

---

## Part D — Release runbook

**Ship a version**

```
# 1. edit VERSION and RELEASE_NOTES.md
python scripts/sync_version.py
# 2. test
python -m pytest tests -q
cd web && npx tsc --noEmit -p . && npx vitest run && cd ..
# 3. commit, then
git tag v<version> && git push origin main v<version>
```

CI takes ~20 minutes and publishes the installer and `latest.json`.
Installed apps find it within the hour, or at next launch.

**Prove an update end to end** (do this for the first release of any new
product, and after touching the updater):

1. Install version N from its published installer.
2. Confirm it reports "no update" while N is the latest.
3. Publish N+1 containing something visible that N lacks.
4. In N: the update is offered with its notes → Install → progress → the app
   exits → it comes back by itself.
5. In N+1: the version (app and engine), the new feature, and "up to date".

To test update *UI* that only a newer build contains, build that code
locally stamped with an older version number and run it against the
published release; restore `VERSION` afterwards.

Result here: 0.2.0 → 0.2.1 in ~10 s of download, no prompts, relaunched as
0.2.1 with the new About section. (DESKTOP_RELEASE_PLAN.md §14.)

---

## Part E — Checklists

**Before a repo goes public:** secret scan clean · licence notes for
committed data re-read · no personal paths · large files acceptable.

**Before each release:** `VERSION` and notes updated · versions in step ·
all tests green · smoke test green locally · tag matches `VERSION`.

**After each release:** CI run green including the runner's smoke test ·
`latest.json` shows the new version · one installed copy updated for real.

**Before telling a user "it works":** tested in the built app, not the dev
server · tested from the installed location · the thing they will see was
looked at (a screenshot), not inferred.

---

## Part F — What to do differently next time

1. **Measure the hosted backend with a real workload on day one.** A health
   check proved nothing; the first real import was killed for memory.
2. **Look for per-item DOM early.** One marker per flight survived two
   rounds of GPU work and caused the last stutter. Count the DOM under the
   map before optimising anything else.
3. **Do not predict a host's behaviour from its published limits** — the
   "more workers will not help on the free plan" prediction was wrong (2.6×).
   Change one setting and measure.
4. **Design update UI for the screen the user is actually on.** A dot on a
   button that is not rendered is not a notification.
5. **Write multi-line edits as script files**, not shell heredocs; quoting
   broke repeatedly on Windows.
6. **Check that a restarted dev server really restarted** (`uvicorn
   --reload` on Windows sometimes keeps the old process); verify with a
   request that shows the new behaviour.
7. **Decide product questions before code** (name, publisher, hosting,
   licensing). They change file layouts and are cheap to ask first.
8. **Start with the single version source and the smoke script.** Both were
   added mid-way and would have caught problems earlier.

---

## File map (what to copy to the complete version)

| Path | Purpose |
|---|---|
| `desktop/sidecar/engine_main.py`, `engine.spec` | Frozen engine entry point and PyInstaller recipe |
| `desktop/src-tauri/src/lib.rs`, `tauri.conf.json`, `Cargo.toml` | Shell: engine lifecycle, config injection, updater, commands |
| `desktop/build.ps1`, `desktop/smoke.ps1` | Build and smoke test |
| `desktop/brand.json`, `desktop/icons/` | Product identity in one place |
| `web/lib/backend.ts` (+ test) | Runtime engine address and token; the `apiFetch` guard test |
| `web/lib/desktop.ts`, `web/components/desktop/*` | Shell bridge, update hook, About dialog, update banner |
| `web/lib/cdr/usePlanScan.ts`, `planScan.worker.ts`, `planScanPacked.ts` | The worker pattern for an O(n²) scan |
| `web/components/DeckOverlay.ts`, `GpuTraffic.tsx`, `FpsMeter.tsx` | GPU map layer, FPS overlay |
| `api/server.py`: local mode, token middleware, `_run_batch`, `_auto_workers` | Engine security, parallel generation, memory-aware pool |
| `api/version.py`, `VERSION`, `scripts/sync_version.py` | One version source |
| `scripts/make_update_manifest.py`, `scripts/make_installer_license.py` | Release folder and installer licence text |
| `.github/workflows/desktop.yml` | Tests and release pipeline |
| `tests/test_session_token.py`, `tests/test_gen_workers.py` | Engine security and pool sizing |
