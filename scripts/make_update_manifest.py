"""Assemble the files a desktop release publishes.

    python scripts/make_update_manifest.py [--notes "text" | --notes-file path] [--out release]

Reads the installer and its updater signature from the Tauri bundle folder and
writes, into ``release/``:

  ATC-FTS_<version>_x64-setup.exe   the installer under a name with no spaces
                                    (GitHub rewrites spaces in asset names, and
                                    the manifest must point at the exact URL)
  latest.json                       what the app's updater fetches:
                                    version, notes, date, and per platform the
                                    download URL and its signature

Both are uploaded as assets of the GitHub release tagged ``v<version>``; the
app looks at ``releases/latest/download/latest.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
REPO = "Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version"
BUNDLE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--out", default=str(ROOT / "release"))
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    installers = sorted(BUNDLE.glob(f"*_{VERSION}_x64-setup.exe"))
    if not installers:
        print(f"no installer for {VERSION} in {BUNDLE} — run desktop/build.ps1 first")
        return 1
    installer = installers[0]
    sig = installer.with_name(installer.name + ".sig")
    if not sig.is_file():
        print(f"{sig.name} is missing — build with TAURI_SIGNING_PRIVATE_KEY set")
        return 1

    notes = Path(args.notes_file).read_text(encoding="utf-8") if args.notes_file else args.notes
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asset = f"ATC-FTS_{VERSION}_x64-setup.exe"
    # Everything in this folder is uploaded to the release: drop installers
    # left by an earlier version's build.
    for stale in out.glob("ATC-FTS_*-setup.exe"):
        if stale.name != asset:
            stale.unlink()
    shutil.copy2(installer, out / asset)

    manifest = {
        "version": VERSION,
        "notes": notes.strip(),
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            "windows-x86_64": {
                "signature": sig.read_text(encoding="utf-8").strip(),
                "url": f"https://github.com/{args.repo}/releases/download/v{VERSION}/{asset}",
            }
        },
    }
    (out / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    size = (out / asset).stat().st_size / 2**20
    print(f"{out / asset}  {size:.1f} MB")
    print(f"{out / 'latest.json'}  -> v{VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
