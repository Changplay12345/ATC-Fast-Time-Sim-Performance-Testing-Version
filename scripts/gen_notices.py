"""Generate the third-party licence notices shipped with the desktop app.

    python scripts/gen_notices.py

Lists what the installer actually contains, from the real dependency data:

  * Python  - the engine's packages: requirements.txt and everything they
              pull in, read from the installed distributions' metadata
  * JS      - the front end's production dependencies, from package-lock.json
  * Rust    - the shell's crates, from `cargo metadata`

and writes desktop/installer/THIRD_PARTY_NOTICES.txt (git-ignored; the build
regenerates it and bundles it; About > Third-party licences opens it).
Plain ASCII text, so Notepad shows it properly on any Windows code page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "desktop" / "installer" / "THIRD_PARTY_NOTICES.txt"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _py_license(dist: metadata.Distribution) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier", []) if c.startswith("License ::")]
    if classifiers:
        return "; ".join(sorted(set(classifiers)))
    lic = (md.get("License") or "").strip()
    # Some packages put the whole licence text in this field.
    return lic.splitlines()[0][:80] if lic else "see package"


def python_packages() -> list[tuple[str, str, str]]:
    roots = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("pytest"):
            continue
        roots.append(re.split(r"[=<>!~\[ ]", line, maxsplit=1)[0])
    seen: dict[str, tuple[str, str, str]] = {}
    todo = list(roots)
    while todo:
        name = todo.pop()
        key = _norm(name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = (dist.metadata["Name"], dist.version, _py_license(dist))
        for req in dist.requires or []:
            # Optional extras are not installed unless asked for.
            if "extra ==" in req:
                continue
            todo.append(re.split(r"[=<>!~;\[ (]", req, maxsplit=1)[0])
    return sorted(seen.values(), key=lambda r: r[0].lower())


def js_packages(lock: Path) -> list[tuple[str, str, str]]:
    data = json.loads(lock.read_text(encoding="utf-8"))
    out = {}
    for path, info in data.get("packages", {}).items():
        if not path or info.get("dev") or info.get("devOptional"):
            continue
        name = path.split("node_modules/")[-1]
        lic = info.get("license") or "see package"
        if isinstance(lic, dict):
            lic = lic.get("type", "see package")
        out[(name, info.get("version", ""))] = str(lic)
    return sorted(((n, v, lic) for (n, v), lic in out.items()), key=lambda r: r[0].lower())


def rust_crates() -> list[tuple[str, str, str]] | None:
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo.exe")
    if not Path(cargo).exists() and not shutil.which("cargo"):
        return None
    res = subprocess.run(
        [cargo, "metadata", "--format-version", "1", "--manifest-path", str(ROOT / "desktop" / "src-tauri" / "Cargo.toml")],
        capture_output=True, text=True, encoding="utf-8",
    )
    if res.returncode != 0:
        return None
    pkgs = json.loads(res.stdout)["packages"]
    rows = {(p["name"], p["version"]): (p.get("license") or "see crate") for p in pkgs if p["name"] != "atc-fts-desktop"}
    return sorted(((n, v, lic) for (n, v), lic in rows.items()), key=lambda r: r[0].lower())


def table(rows: list[tuple[str, str, str]]) -> str:
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    return "\n".join(f"  {n.ljust(w0)}  {v.ljust(w1)}  {lic}" for n, v, lic in rows)


HEADER = """ATC FAST-TIME SIMULATION TOOL - THIRD-PARTY NOTICES

This application includes or links to the open-source software listed below.
Each component is the property of its authors and is distributed under its
own licence, named next to it. Licence texts ship with the components inside
the application folder (engine\\_internal\\<package>.dist-info) and are
available from each project.

COMPONENTS THAT NEED A SPECIFIC NOTE

  Python (CPython)      Python Software Foundation License
  PyInstaller           The bootloader that starts the engine is GPL-2.0 with
                        an exception that permits distributing applications
                        built with it under any licence.
  GDAL / OGR            MIT. Bundled inside the pyogrio package.
  PROJ                  MIT. Bundled inside the pyproj and pyogrio packages,
                        with the EPSG dataset (proj.db).
  GEOS                  LGPL-2.1. Bundled as a separate DLL inside the shapely
                        package; it can be replaced by a compatible build.
  Microsoft WebView2    The window is drawn by the Microsoft Edge WebView2
                        runtime, part of Windows; Microsoft's terms apply.
  Map tiles             Basemap imagery is loaded at runtime from Esri's
                        public tile services and remains the property of
                        Esri and its data providers.
  Navigation data       See DATA_SOURCES.md in the project repository.
"""


def main() -> int:
    parts = [HEADER]
    py = python_packages()
    parts.append(f"\nPYTHON PACKAGES IN THE ENGINE ({len(py)})\n\n" + table(py))
    js = js_packages(ROOT / "web" / "package-lock.json")
    parts.append(f"\n\nJAVASCRIPT PACKAGES IN THE FRONT END ({len(js)})\n\n" + table(js))
    rust = rust_crates()
    if rust is None:
        if "--allow-missing-cargo" not in sys.argv:
            print("cargo not found: cannot list the shell's crates (use --allow-missing-cargo to skip)")
            return 1
        parts.append("\n\nRUST CRATES IN THE SHELL\n\n  (not listed: cargo was not available when this file was generated)")
    else:
        parts.append(f"\n\nRUST CRATES IN THE SHELL ({len(rust)})\n\n" + table(rust))
    text = "\n".join(parts) + "\n"
    text = text.encode("ascii", "replace").decode("ascii")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="ascii", newline="\r\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(py)} python, {len(js)} js, {len(rust or [])} rust")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
