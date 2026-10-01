# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the engine sidecar (one-folder build).

Build from the repo root:
    python -m PyInstaller --noconfirm --clean desktop/sidecar/engine.spec \
        --distpath desktop/sidecar/dist --workpath desktop/sidecar/build

One-folder, not one-file: starts in about a second (no unpacking to a temp
folder each launch), fewer antivirus false positives, and what macOS
notarisation handles. UPX is off for the same antivirus reason.

Data layout inside dist/atc-engine/_internal mirrors the repo, because the
engine resolves its data relative to its own source files:
  api/server.py          -> _ROOT = parent.parent = _internal
                            _ROOT/web/public/data, _ROOT/thai_aip_…csv
  trajectory_sim/*.py    -> Path(__file__).parent/data
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

datas = [
    # Navdata the API reads (AIP cache, airports, airways, AIXM procedures,
    # sector polygons, FIR, holdings) — ~42 MB.
    (os.path.join(ROOT, "web", "public", "data"), os.path.join("web", "public", "data")),
    # Aircraft performance tables + CAT62 reference — ~0.5 MB.
    (os.path.join(ROOT, "trajectory_sim", "data"), os.path.join("trajectory_sim", "data")),
    (os.path.join(ROOT, "thai_aip_ad2_thr_elevations.csv"), "."),
]
# pyogrio has no PyInstaller hook; its wheel carries GDAL's and PROJ's data
# folders and points GDAL at them itself as long as they sit next to the
# package. pyproj's proj.db is handled by the stock pyproj hook.
datas += collect_data_files("pyogrio", includes=["gdal_data/**", "proj_data/**"])

hiddenimports = collect_submodules("pyogrio", filter=lambda name: ".tests" not in name) + [
    # uvicorn picks these at runtime by name.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # the batch generator's worker pool
    "concurrent.futures.process",
    "multiprocessing.pool",
    "multiprocessing.popen_spawn_win32",
]

excludes = [
    "matplotlib", "IPython", "jupyter", "notebook", "sphinx", "pytest",
    "tkinter", "_tkinter", "PIL", "PyQt5", "PySide2", "scipy", "numba",
    "pandas.tests", "numpy.tests", "pyogrio.tests",
]

a = Analysis(
    [os.path.join(SPECPATH, "engine_main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="atc-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # the shell captures stdout/stderr into its log; hidden window
    icon=os.path.join(ROOT, "desktop", "icons", "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="atc-engine",
)
