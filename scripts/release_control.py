"""Control a desktop release after it has been published.

    python scripts/release_control.py status
    python scripts/release_control.py rollout 25            # offer it to 25 % of installations
    python scripts/release_control.py rollout 100           # ...to everybody
    python scripts/release_control.py require 0.4.1         # versions below 0.4.1 must update
    python scripts/release_control.py require none
    python scripts/release_control.py halt                  # stop offering the latest release
    python scripts/release_control.py resume --tag v0.4.1   # offer it again

``rollout`` and ``require`` edit two fields of the release's ``latest.json``
(the file installed apps poll; see ``desktop/src-tauri/src/release.rs`` for
how the app reads them). They act on the release that is "latest" unless
``--tag`` names another. The manifest is not signed - the installer is - so
it can be edited in place; apps pick the change up at their next check
(a few seconds after launch, then hourly).

GitHub serves an edited or re-pointed manifest about two minutes after the
change (measured: 105 s). Each command therefore waits until the public URL
really serves the new state and says how long it took; ``--no-wait`` skips
that.

``halt`` withdraws a release: the previous version becomes "latest" again and
the halted one is marked a pre-release, so no app is offered it any more.
Apps that already installed it keep it - there is no downgrade - so follow a
halt with a fixed release. ``resume`` undoes a halt.

Needs the GitHub CLI (``gh``), signed in with write access to the repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version"
MANIFEST = "latest.json"


def gh_exe() -> str:
    found = shutil.which("gh") or shutil.which("gh.exe")
    if found:
        return found
    fallback = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if fallback.is_file():
        return str(fallback)
    raise SystemExit("the GitHub CLI (gh) is not installed")


def gh(*args: str) -> str:
    done = subprocess.run([gh_exe(), *args, "--repo", REPO], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


def key(tag: str) -> tuple[int, ...]:
    """``v0.10.2`` -> ``(0, 10, 2)``; anything else sorts first."""
    try:
        return tuple(int(p) for p in tag.lstrip("v").split("."))
    except ValueError:
        return ()


def app_releases() -> list[dict]:
    """Published app releases (tags ``v*``), newest version first."""
    rows = json.loads(gh("release", "list", "--limit", "50", "--json", "tagName,isLatest,isPrerelease,isDraft"))
    rows = [r for r in rows if r["tagName"].startswith("v") and key(r["tagName"]) and not r["isDraft"]]
    return sorted(rows, key=lambda r: key(r["tagName"]), reverse=True)


def latest_tag() -> str:
    for r in app_releases():
        if r["isLatest"]:
            return r["tagName"]
    raise SystemExit('no app release is marked "latest"')


def read_manifest(tag: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        gh("release", "download", tag, "--pattern", MANIFEST, "--dir", tmp)
        return json.loads((Path(tmp) / MANIFEST).read_text(encoding="utf-8"))


def write_manifest(tag: str, manifest: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / MANIFEST
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        gh("release", "upload", tag, str(path), "--clobber")


def served() -> dict | None:
    """The manifest installed apps are being served right now."""
    url = f"https://github.com/{REPO}/releases/latest/download/{MANIFEST}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def wait_served(args: argparse.Namespace, want: dict) -> None:
    """Block until the public URL serves a manifest whose fields equal
    ``want`` (a key mapped to None must be absent)."""
    if args.no_wait:
        print("not waiting; apps see the change in about two minutes")
        return
    started = time.time()
    while time.time() - started < 600:
        now = served()
        if now is not None and all(now.get(k) == v for k, v in want.items()):
            print(f"apps are being served the change after {time.time() - started:.0f} s")
            return
        time.sleep(5)
    raise SystemExit("the change is still not being served after 10 minutes - check the release by hand")


def describe(manifest: dict) -> str:
    return (
        f"version {manifest.get('version')}, rollout {manifest.get('rollout', 100)} %, "
        f"minimum supported {manifest.get('min_supported') or 'none'}"
    )


def cmd_status(_: argparse.Namespace) -> None:
    for r in app_releases():
        flags = [f for f, on in (("latest", r["isLatest"]), ("pre-release (halted)", r["isPrerelease"])) if on]
        print(f"  {r['tagName']:<10} {', '.join(flags)}")
    url = f"https://github.com/{REPO}/releases/latest/download/{MANIFEST}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            print(f"what apps are served now: {describe(json.load(r))}")
    except OSError as e:
        print(f"what apps are served now: could not be read ({e})")


def cmd_rollout(args: argparse.Namespace) -> None:
    if not 0 <= args.percent <= 100:
        raise SystemExit("the rollout is a percentage, 0 to 100")
    tag = args.tag or latest_tag()
    manifest = read_manifest(tag)
    manifest["rollout"] = args.percent
    write_manifest(tag, manifest)
    print(f"{tag}: {describe(manifest)}")
    if tag == latest_tag():
        wait_served(args, {"version": manifest.get("version"), "rollout": args.percent})


def cmd_require(args: argparse.Namespace) -> None:
    tag = args.tag or latest_tag()
    manifest = read_manifest(tag)
    if args.version.lower() == "none":
        manifest.pop("min_supported", None)
    else:
        if not key(args.version):
            raise SystemExit(f"not a version: {args.version}")
        if key(args.version) > key(str(manifest.get("version", ""))):
            raise SystemExit(
                f"{tag} is {manifest.get('version')}: it cannot require {args.version}, "
                "a version nobody can update to from it"
            )
        manifest["min_supported"] = args.version.lstrip("v")
    write_manifest(tag, manifest)
    print(f"{tag}: {describe(manifest)}")
    if tag == latest_tag():
        wait_served(args, {"version": manifest.get("version"), "min_supported": manifest.get("min_supported")})


def cmd_halt(args: argparse.Namespace) -> None:
    releases = app_releases()
    tag = args.tag or latest_tag()
    older = [r for r in releases if key(r["tagName"]) < key(tag) and not r["isPrerelease"]]
    if not older:
        raise SystemExit(f"there is no earlier release to fall back to from {tag}")
    previous = older[0]["tagName"]
    # The earlier release first: "latest" is never left pointing at nothing
    # (or at the data release).
    gh("release", "edit", previous, "--latest")
    gh("release", "edit", tag, "--prerelease")
    print(f"{tag} halted. Apps are now served {previous}; nobody is offered {tag}.")
    print(f"Installations already on {tag} keep it: publish a fixed release next.")
    wait_served(args, {"version": previous.lstrip("v")})


def cmd_resume(args: argparse.Namespace) -> None:
    if not args.tag:
        raise SystemExit("say which release to resume: --tag v0.4.1")
    gh("release", "edit", args.tag, "--prerelease=false", "--latest")
    print(f"{args.tag} is offered again.")
    wait_served(args, {"version": args.tag.lstrip("v")})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-wait", action="store_true", help="do not wait until the change is being served")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(run=cmd_status)
    p = sub.add_parser("rollout")
    p.add_argument("percent", type=int)
    p.add_argument("--tag")
    p.set_defaults(run=cmd_rollout)
    p = sub.add_parser("require")
    p.add_argument("version", help='a version, or "none"')
    p.add_argument("--tag")
    p.set_defaults(run=cmd_require)
    p = sub.add_parser("halt")
    p.add_argument("--tag")
    p.set_defaults(run=cmd_halt)
    p = sub.add_parser("resume")
    p.add_argument("--tag")
    p.set_defaults(run=cmd_resume)
    args = ap.parse_args()
    args.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
