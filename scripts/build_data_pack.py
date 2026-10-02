"""Build a navigation-data pack and the manifest installed apps poll.

    python scripts/build_data_pack.py --version 2026.10.01.1 --airac 2026-10-01 \
        --notes "AIRAC 2026-10-01"

A data pack replaces the program's bundled data tree (``web/public/data``)
without a new program: the desktop shell downloads it, checks it, unpacks it
into the user's data folder and starts the engine on it the next time the app
is opened. See ``trajectory_sim/datapaths.py`` for the engine's side.

Writes, into ``release-data/``:

  atc-data_<version>.zip   the data tree, with a ``pack.json`` describing it
  data-manifest.json       version, AIRAC cycle, minimum app version, the
                           pack's URL, size, SHA-256 and signature

Both are uploaded as assets of the GitHub release tagged ``data`` (the pack
first, the manifest last, so the manifest never points at a missing file).

The pack is signed with the same key as app updates (``tauri signer``); an
app only accepts a pack whose signature matches the public key built into it,
so neither a tampered pack nor a forged manifest gets through. The key comes
from ``$TAURI_SIGNING_PRIVATE_KEY`` (CI) or ``~/.tauri/atc-fts-updater.key``.
``--unsigned`` skips signing, for tests of the app's refusal only.

The build is reproducible: same data and arguments, same bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trajectory_sim.datapaths import PACK_FILE, PACK_SCHEMA, read_pack_file, version_key  # noqa: E402

REPO = "Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version"
SOURCE = ROOT / "web" / "public" / "data"
#: The release tag the data lives under. Not ``latest``: that one is the app.
DATA_TAG = "data"
_FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # zip entries carry no build time


def build_zip(source: Path, meta: dict, out: Path) -> None:
    """Zip ``source`` with ``meta`` as its pack.json. Entries are sorted and
    carry a fixed timestamp so the same input gives the same archive."""
    files = sorted(
        p for p in source.rglob("*") if p.is_file() and p.relative_to(source).as_posix() != PACK_FILE
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:

        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, _FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)

        add(PACK_FILE, (json.dumps(meta, indent=2) + "\n").encode())
        for p in files:
            add(p.relative_to(source).as_posix(), p.read_bytes())


def sign(path: Path) -> str:
    """Sign ``path`` with the updater key; returns the signature string that
    goes into the manifest (what ``tauri signer`` writes to ``<file>.sig``)."""
    env = dict(os.environ)
    if not env.get("TAURI_SIGNING_PRIVATE_KEY"):
        key = Path.home() / ".tauri" / "atc-fts-updater.key"
        if not key.is_file():
            raise SystemExit(f"signing key not found: {key} (or set TAURI_SIGNING_PRIVATE_KEY)")
        env["TAURI_SIGNING_PRIVATE_KEY"] = key.read_text(encoding="utf-8").strip()
    env.setdefault("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise SystemExit("npx not found - Node is needed to sign (tauri signer)")
    done = subprocess.run(
        [npx, "tauri", "signer", "sign", str(path)],
        cwd=ROOT / "desktop", env=env, capture_output=True, text=True,
    )
    sig = path.with_name(path.name + ".sig")
    if done.returncode != 0 or not sig.is_file():
        raise SystemExit(f"signing failed:\n{done.stdout}\n{done.stderr}")
    text = sig.read_text(encoding="utf-8").strip()
    sig.unlink()  # it lives in the manifest; a stray .sig would be uploaded
    return text


def main() -> int:
    own = read_pack_file(SOURCE)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", default=own.get("version"), help="AIRAC date + build, e.g. 2026.10.01.1")
    ap.add_argument("--airac", default=own.get("airac"))
    ap.add_argument("--min-app", default=own.get("min_app", "0.3.0"), help="oldest app version that can read it")
    ap.add_argument("--notes", default="")
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--out", default=str(ROOT / "release-data"))
    ap.add_argument("--base-url", default=f"https://github.com/{REPO}/releases/download/{DATA_TAG}")
    ap.add_argument("--unsigned", action="store_true")
    args = ap.parse_args()

    if not version_key(args.version):
        print(f"not a version: {args.version!r} (want e.g. 2026.10.01.1)")
        return 1
    source = Path(args.source)
    if not (source / "aip_VT.json").is_file():
        print(f"{source} is not a data tree (no aip_VT.json)")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("atc-data_*.zip*"):
        stale.unlink()
    meta = {"schema": PACK_SCHEMA, "version": args.version, "airac": args.airac, "min_app": args.min_app}
    name = f"atc-data_{args.version}.zip"
    pack = out / name
    build_zip(source, meta, pack)

    blob = pack.read_bytes()
    manifest = {
        **meta,
        "notes": args.notes.strip(),
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": f"{args.base_url.rstrip('/')}/{name}",
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "signature": "" if args.unsigned else sign(pack),
    }
    (out / "data-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{pack}  {len(blob) / 2**20:.1f} MB  sha256 {manifest['sha256'][:16]}...")
    print(f"{out / 'data-manifest.json'}  -> data {args.version}" + ("  (UNSIGNED)" if args.unsigned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
