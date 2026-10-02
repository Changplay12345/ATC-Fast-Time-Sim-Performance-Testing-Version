"""End-to-end test of navigation-data packs against the built desktop app.

    .venv\\Scripts\\python desktop\\datapack_e2e.py            # the built exe
    .venv\\Scripts\\python desktop\\datapack_e2e.py --exe "<path to atc-fts-desktop.exe>"

Serves packs from a local web server and points the app at it with
``ATC_DATA_MANIFEST_URL`` (the app still demands our signature, so this
proves the real checks). Needs the signing key (``~/.tauri/atc-fts-updater.key``
or ``$TAURI_SIGNING_PRIVATE_KEY``) and Node, because it builds real packs with
``scripts/build_data_pack.py``.

Checks:
  1. a newer, properly signed pack is downloaded, verified and selected while
     the app runs on its bundled data
  2. after a restart the engine runs on that pack
  3. a pack whose bytes were altered is refused (hash)
  4. a pack re-described to match its altered bytes is refused (signature)
  5. an unsigned pack is refused
  6. a pack that needs a newer app is left alone
  7. a pack that is not newer than the data in use is left alone
  8. a selected pack that is broken on disk does not stop the app: it starts
     on the bundled data and the pack is deselected

It uses the app's real data folder and removes ``data-packs`` from it before
and after, so no test pack is left selected.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from trajectory_sim.datapaths import read_pack_file  # noqa: E402

APP_DATA = Path(os.environ["LOCALAPPDATA"]) / "th.co.bearcat.atcfts"
PACKS = APP_DATA / "data-packs"
SHELL_LOG = APP_DATA / "logs" / "ATC Fast-Time Simulation Tool.log"
BUNDLED = read_pack_file(REPO / "web" / "public" / "data")
FIRST_CHECK_S = 8  # datapack.rs FIRST_CHECK

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"  {'ok  ' if ok else 'FAIL'}  {name} {detail}")
    if not ok:
        failed += 1


def stop_all() -> None:
    for name in ("atc-fts-desktop.exe", "atc-engine.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
    time.sleep(0.7)


def engine_port(app_pid: int, timeout: float = 90) -> int | None:
    """The port the engine started by ``app_pid`` listens on."""
    script = (
        f"$e = Get-CimInstance Win32_Process | Where-Object {{ $_.Name -eq 'atc-engine.exe' -and $_.ParentProcessId -eq {app_pid} }} | Select-Object -First 1;"
        "if ($e) { (Get-NetTCPConnection -OwningProcess $e.ProcessId -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort }"
    )
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout.strip()
        if out.isdigit():
            return int(out)
        time.sleep(0.5)
    return None


def health(port: int) -> dict:
    end = time.time() + 60
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                return json.load(r)
        except OSError:
            if time.time() > end:
                raise
            time.sleep(0.5)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # one line per request is noise here
        pass


class App:
    """One run of the desktop app; ``log`` is what the shell logged during it."""

    def __init__(self, exe: Path, manifest_url: str | None):
        stop_all()
        self._log_from = SHELL_LOG.stat().st_size if SHELL_LOG.is_file() else 0
        env = dict(os.environ)
        env.pop("ATC_DATA_MANIFEST_URL", None)
        if manifest_url:
            env["ATC_DATA_MANIFEST_URL"] = manifest_url
        self.proc = subprocess.Popen([str(exe)], env=env)
        port = engine_port(self.proc.pid)
        if port is None:
            raise SystemExit("the app did not bring its engine up")
        self.health = health(port)

    @property
    def log(self) -> str:
        if not SHELL_LOG.is_file():
            return ""
        with SHELL_LOG.open("rb") as f:
            f.seek(self._log_from)
            return f.read().decode("utf-8", "replace")

    def wait_for_log(self, text: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if text in self.log:
                return True
            time.sleep(0.5)
        return False

    def close(self) -> None:
        stop_all()


def build_pack(out: Path, base_url: str, version: str, *extra: str) -> dict:
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_data_pack.py"), "--version", version,
         "--out", str(out), "--base-url", base_url, "--notes", f"test pack {version}", *extra],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"pack build failed:\n{done.stdout}\n{done.stderr}")
    return json.loads((out / "data-manifest.json").read_text(encoding="utf-8"))


def write_manifest(out: Path, manifest: dict) -> None:
    (out / "data-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def selected() -> str | None:
    try:
        return json.loads((PACKS / "current.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def refused(exe: Path, url: str, name: str, expect_in_log: str | None) -> None:
    """Run the app against the manifest at ``url`` and check nothing was
    selected (and, when a reason is expected, that the log gives it)."""
    shutil.rmtree(PACKS, ignore_errors=True)
    app = App(exe, url)
    try:
        if expect_in_log:
            said = app.wait_for_log(expect_in_log, FIRST_CHECK_S + 40)
            check(name, said and selected() is None, f"(log: {'...' + expect_in_log + '...' if said else 'reason not logged'})")
        else:
            time.sleep(FIRST_CHECK_S + 10)
            check(name, selected() is None and "downloading data pack" not in app.log)
    finally:
        app.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(REPO / "desktop" / "src-tauri" / "target" / "release" / "atc-fts-desktop.exe"))
    args = ap.parse_args()
    exe = Path(args.exe)
    if not exe.is_file():
        raise SystemExit(f"app not found: {exe}")

    parts = BUNDLED["version"].split(".")
    newer = ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])
    work = Path(tempfile.mkdtemp(prefix="atc-pack-e2e-"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(work)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    url = f"{base}/data-manifest.json"
    print(f"bundled data {BUNDLED['version']}; test pack {newer}; serving {work} at {base}")

    try:
        # 1 + 2: the good path -------------------------------------------------
        shutil.rmtree(PACKS, ignore_errors=True)
        good = build_pack(work, base, newer)
        app = App(exe, url)
        check("the app starts on its bundled data", app.health.get("data_source") == "bundled"
              and app.health.get("data_version") == BUNDLED["version"], f"({app.health.get('data_version')})")
        t0 = time.time()
        while selected() != newer and time.time() - t0 < FIRST_CHECK_S + 60:
            time.sleep(0.5)
        check("a newer signed pack is downloaded and selected", selected() == newer, f"({time.time() - t0:.0f} s after launch)")
        check("it is unpacked whole", (PACKS / newer / "aip_VT.json").is_file()
              and read_pack_file(PACKS / newer).get("version") == newer)
        check("the running engine is not switched", health_of(app).get("data_source") == "bundled")
        app.close()

        app = App(exe, url)
        check("after a restart the engine runs on the pack", app.health.get("data_source") == "pack"
              and app.health.get("data_version") == newer, f"({app.health.get('data_version')}, {app.health.get('data_source')})")
        time.sleep(FIRST_CHECK_S + 6)
        check("the same pack is not downloaded again", "downloading data pack" not in app.log)
        app.close()

        # 3: bytes altered after signing -> hash --------------------------------
        pack = work / f"atc-data_{newer}.zip"
        original = pack.read_bytes()
        altered = bytearray(original)
        altered[len(altered) // 2] ^= 0xFF
        pack.write_bytes(bytes(altered))
        refused(exe, url, "an altered pack is refused (hash)", "SHA-256 does not match")

        # 4: manifest rewritten to match the altered bytes -> signature ---------
        write_manifest(work, {**good, "sha256": hashlib.sha256(bytes(altered)).hexdigest()})
        refused(exe, url, "an altered pack with a matching manifest is refused (signature)", "signature does not match")

        # 5: unsigned -----------------------------------------------------------
        pack.write_bytes(original)
        write_manifest(work, {**good, "signature": ""})
        refused(exe, url, "an unsigned pack is refused", "not signed")

        # 6: needs a newer app --------------------------------------------------
        write_manifest(work, {**good, "min_app": "99.0.0"})
        refused(exe, url, "a pack that needs a newer app is left alone", "needs app 99.0.0")

        # 7: not newer than the data in use -------------------------------------
        write_manifest(work, {**good, "version": BUNDLED["version"]})
        refused(exe, url, "a pack that is not newer is left alone", None)

        # 8: a selected pack that is broken on disk -----------------------------
        shutil.rmtree(PACKS, ignore_errors=True)
        broken = PACKS / newer
        broken.mkdir(parents=True)
        (broken / "pack.json").write_text(json.dumps({"schema": 1, "version": newer}), encoding="utf-8")
        (PACKS / "current.json").write_text(json.dumps({"version": newer}), encoding="utf-8")
        app = App(exe, None)
        check("a broken selected pack does not stop the app", app.health.get("ok") is True
              and app.health.get("data_source") == "bundled", f"({app.health.get('data_version')}, {app.health.get('data_source')})")
        check("and it is deselected", selected() is None)
        app.close()
    finally:
        stop_all()
        server.shutdown()
        shutil.rmtree(PACKS, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{failed} check(s) failed" if failed else "\ndata pack test passed")
    return 1 if failed else 0


def health_of(app: App) -> dict:
    port = engine_port(app.proc.pid, timeout=10)
    return health(port) if port else {}


if __name__ == "__main__":
    sys.exit(main())
