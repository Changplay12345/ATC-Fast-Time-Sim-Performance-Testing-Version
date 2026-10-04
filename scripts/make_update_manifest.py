"""Assemble the files a desktop release publishes.

    python scripts/make_update_manifest.py [--notes-file path] [--out release]
                                           [--search DIR ...] [--require windows,macos]

Looks for the built installers and their updater signatures, copies them into
``release/`` under names with no spaces (GitHub rewrites spaces in asset
names, and the manifest must point at the exact URL), and writes
``latest.json``, the file installed apps poll:

  ATC-FTS_<version>_x64-setup.exe           Windows installer (+ .sig)
  ATC-FTS_<version>_macos-arm64.app.tar.gz  macOS, what the updater downloads (+ .sig)
  ATC-FTS_<version>_macos-arm64.zip         macOS, what a person downloads
  latest.json                               version, notes, date, the release
                                            policy (desktop/release.json),
                                            and per platform the download URL
                                            and its signature

Where it looks: the Tauri bundle folder and ``release/`` itself, plus any
``--search`` folders (CI passes the folder its build jobs' artifacts were
downloaded into, so one manifest covers both platforms). Only the platforms
found are listed; ``--require`` makes a missing one an error.

Everything in ``release/`` is uploaded as assets of the GitHub release tagged
``v<version>``; the app looks at ``releases/latest/download/latest.json``.
The policy fields ("rollout", "min_supported") can be changed after
publishing with scripts/release_control.py.
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
BUNDLE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle"
POLICY = ROOT / "desktop" / "release.json"

# Updater target -> how to recognise the built file, and its published name.
PLATFORMS = {
    "windows-x86_64": {
        "key": "windows",
        "pattern": f"*_{VERSION}_x64-setup.exe",
        "asset": f"ATC-FTS_{VERSION}_x64-setup.exe",
    },
    "darwin-aarch64": {
        "key": "macos",
        "pattern": "*.app.tar.gz",
        "asset": f"ATC-FTS_{VERSION}_macos-arm64.app.tar.gz",
    },
}
# Not in the manifest, but published beside it.
EXTRA = [f"ATC-FTS_{VERSION}_macos-arm64.zip"]


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


def find(search: list[Path], pattern: str) -> Path | None:
    """The first file matching ``pattern`` under the search folders that has
    its updater signature beside it."""
    for folder in search:
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.rglob(pattern)):
            if candidate.is_file() and candidate.with_name(candidate.name + ".sig").is_file():
                return candidate
    return None


def place(src: Path, dest: Path) -> None:
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--out", default=str(ROOT / "release"))
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--search", action="append", default=[], help="extra folder to look in (recursively)")
    ap.add_argument("--require", default="", help="comma-separated: windows, macos")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    search = [Path(s) for s in args.search] + [BUNDLE, out]

    # Everything in this folder is uploaded to the release: drop files left
    # by an earlier version's build.
    for stale in out.glob("ATC-FTS_*"):
        if f"_{VERSION}_" not in stale.name:
            stale.unlink()

    found: dict[str, Path] = {}
    for target, spec in PLATFORMS.items():
        src = find(search, spec["pattern"])
        if src is None:
            continue
        dest = out / spec["asset"]
        place(src, dest)
        place(src.with_name(src.name + ".sig"), dest.with_name(dest.name + ".sig"))
        found[target] = dest
    for name in EXTRA:
        for folder in search:
            hit = next((p for p in folder.rglob(name) if p.is_file()), None) if folder.is_dir() else None
            if hit:
                place(hit, out / name)
                break

    required = {k.strip() for k in args.require.split(",") if k.strip()}
    missing = [spec["key"] for target, spec in PLATFORMS.items() if spec["key"] in required and target not in found]
    if missing:
        print(f"no build found for: {', '.join(missing)} (version {VERSION}; looked in {', '.join(map(str, search))})")
        return 1
    if not found:
        print(f"no installer for {VERSION} in {BUNDLE} - run desktop/build.ps1 first")
        return 1

    notes = Path(args.notes_file).read_text(encoding="utf-8") if args.notes_file else args.notes
    manifest = {
        "version": VERSION,
        "notes": notes.strip(),
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **release_policy(),
        "platforms": {
            target: {
                "signature": dest.with_name(dest.name + ".sig").read_text(encoding="utf-8").strip(),
                "url": f"https://github.com/{args.repo}/releases/download/v{VERSION}/{dest.name}",
            }
            for target, dest in found.items()
        },
    }
    (out / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for dest in found.values():
        print(f"{dest}  {dest.stat().st_size / 2**20:.1f} MB")
    print(
        f"{out / 'latest.json'}  -> v{VERSION}, platforms {', '.join(found)}, "
        f"rollout {manifest['rollout']} %, minimum supported {manifest.get('min_supported', 'none')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
