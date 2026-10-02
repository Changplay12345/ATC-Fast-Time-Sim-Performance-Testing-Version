"""Assemble the files a desktop release publishes.

    python scripts/make_update_manifest.py [--notes "text" | --notes-file path] [--out release]

Reads the installer and its updater signature from the Tauri bundle folder and
writes, into ``release/``:

  ATC-FTS_<version>_x64-setup.exe   the installer under a name with no spaces
                                    (GitHub rewrites spaces in asset names, and
                                    the manifest must point at the exact URL)
  latest.json                       what the app's updater fetches:
                                    version, notes, date, and per platform the
                                    download URL and its signature; plus the
                                    release policy from desktop/release.json:
                                    "rollout" (the share of installations
                                    offered it, 0-100) and "min_supported"
                                    (versions below it must update). Both can
                                    be changed after publishing with
                                    scripts/release_control.py.

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
POLICY = ROOT / "desktop" / "release.json"


def _key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


def release_policy() -> dict:
    """The policy fields for the manifest, checked. A mistake here reaches
    every installed app, so it stops the build instead."""
    policy = json.loads(POLICY.read_text(encoding="utf-8")) if POLICY.is_file() else {}
    rollout = policy.get("rollout", 100)
    if not isinstance(rollout, int) or isinstance(rollout, bool) or not 0 <= rollout <= 100:
        raise SystemExit(f"{POLICY.name}: rollout must be a whole number 0-100, not {rollout!r}")
    out: dict = {"rollout": rollout}
    minimum = (policy.get("min_supported") or "").strip()
    if minimum:
        if not _key(minimum):
            raise SystemExit(f"{POLICY.name}: min_supported is not a version: {minimum!r}")
        if _key(minimum) > _key(VERSION):
            raise SystemExit(
                f"{POLICY.name}: min_supported {minimum} is newer than this release ({VERSION}); "
                "nobody could reach it"
            )
        out["min_supported"] = minimum
    return out


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
        **release_policy(),
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
    print(
        f"{out / 'latest.json'}  -> v{VERSION}, rollout {manifest['rollout']} %, "
        f"minimum supported {manifest.get('min_supported', 'none')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
