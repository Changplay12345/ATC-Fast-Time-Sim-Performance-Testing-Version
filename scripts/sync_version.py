"""Keep every version string in step with the repo's ``VERSION`` file.

    python scripts/sync_version.py          # write VERSION into the manifests
    python scripts/sync_version.py --check  # exit 1 if any of them disagrees

The engine reads ``VERSION`` at runtime (api/version.py); the desktop shell's
manifests cannot, so they are stamped here: ``desktop/src-tauri/Cargo.toml``,
``desktop/src-tauri/tauri.conf.json`` and ``desktop/package.json``. CI runs
``--check`` so a release can never ship a shell and an engine that disagree.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

CARGO = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
TAURI = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
PKG = ROOT / "desktop" / "package.json"

_CARGO_RE = re.compile(r'(?m)^(version\s*=\s*)"[^"]*"')


def current() -> dict[str, str]:
    cargo = _CARGO_RE.search(CARGO.read_text(encoding="utf-8"))
    return {
        str(CARGO.relative_to(ROOT)): cargo.group(0).split('"')[1] if cargo else "?",
        str(TAURI.relative_to(ROOT)): json.loads(TAURI.read_text(encoding="utf-8"))["version"],
        str(PKG.relative_to(ROOT)): json.loads(PKG.read_text(encoding="utf-8"))["version"],
    }


def write() -> None:
    text = CARGO.read_text(encoding="utf-8")
    # Only the [package] version: the first `version = "…"` line in the file.
    CARGO.write_text(_CARGO_RE.sub(rf'\g<1>"{VERSION}"', text, count=1), encoding="utf-8", newline="\n")
    for path in (TAURI, PKG):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = VERSION
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", VERSION):
        print(f"VERSION {VERSION!r} is not SemVer")
        return 1
    if "--check" in sys.argv:
        bad = {k: v for k, v in current().items() if v != VERSION}
        for k, v in bad.items():
            print(f"{k}: {v} != VERSION {VERSION}")
        print("versions in step" if not bad else "run: python scripts/sync_version.py")
        return 1 if bad else 0
    write()
    print(f"stamped {VERSION} into: " + ", ".join(current()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
