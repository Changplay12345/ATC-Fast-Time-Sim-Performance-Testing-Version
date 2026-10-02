"""Which data tree the engine uses: a data pack if it is usable, the bundled
data otherwise — never a broken pack."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from trajectory_sim import datapaths
from trajectory_sim.datapaths import resolve, version_key

REPO = Path(__file__).resolve().parent.parent
BUNDLED = REPO / "web" / "public" / "data"


def make_tree(root: Path, *, pack: dict | None, sentinel: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if sentinel:
        (root / "aip_VT.json").write_text("{}", encoding="utf-8")
    if pack is not None:
        (root / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    return root


GOOD = {"schema": 1, "version": "2026.10.01.2", "airac": "2026-10-01", "min_app": "0.3.0"}


def test_the_bundled_tree_describes_itself():
    pack = resolve(None)
    assert pack.source == "bundled"
    assert pack.path == BUNDLED
    assert version_key(pack.version), "web/public/data/pack.json needs a version"
    assert pack.rejected is None


def test_a_usable_pack_is_used(tmp_path):
    tree = make_tree(tmp_path / "pack", pack=GOOD)
    pack = resolve(str(tree))
    assert (pack.source, pack.path, pack.version, pack.airac) == ("pack", tree, "2026.10.01.2", "2026-10-01")


def test_a_broken_pack_falls_back_to_the_bundled_data(tmp_path):
    cases = {
        "missing folder": tmp_path / "nowhere",
        "no data in it": make_tree(tmp_path / "a", pack=GOOD, sentinel=False),
        "no pack.json": make_tree(tmp_path / "b", pack=None),
        "unknown schema": make_tree(tmp_path / "c", pack={**GOOD, "schema": 99}),
        "no version": make_tree(tmp_path / "d", pack={"schema": 1}),
        "garbled version": make_tree(tmp_path / "e", pack={**GOOD, "version": "latest"}),
    }
    for name, tree in cases.items():
        pack = resolve(str(tree))
        assert pack.source == "bundled", name
        assert pack.path == BUNDLED, name
        assert pack.rejected, f"{name}: the reason must be reported"

    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "aip_VT.json").write_text("{}", encoding="utf-8")
    (tmp_path / "f" / "pack.json").write_text("{not json", encoding="utf-8")
    assert resolve(str(tmp_path / "f")).source == "bundled"


def test_a_pack_older_than_the_bundled_data_is_not_used(tmp_path):
    # After a program update the bundled data can be newer than a pack
    # downloaded earlier.
    bundled = make_tree(tmp_path / "bundled", pack={**GOOD, "version": "2026.10.29.1"})
    same = make_tree(tmp_path / "same", pack={**GOOD, "version": "2026.10.29.1"})
    older = make_tree(tmp_path / "older", pack=GOOD)
    newer = make_tree(tmp_path / "newer", pack={**GOOD, "version": "2026.10.29.2"})
    assert resolve(str(older), bundled).source == "bundled"
    assert resolve(str(same), bundled).source == "bundled"
    assert "not newer" in resolve(str(older), bundled).rejected
    assert resolve(str(newer), bundled).path == newer


def test_versions_compare_by_number_not_by_text():
    assert version_key("2026.10.01.10") > version_key("2026.10.01.9")
    assert version_key("2026.10.01.1") > version_key("2026.09.03.7")
    assert version_key(None) < version_key("2026.09.03.1")
    assert version_key("nonsense") < version_key("2026.09.03.1")


def test_the_engine_reads_and_serves_the_pack_it_is_given(tmp_path):
    """End to end in a fresh process, as the shell starts it: with
    $ATC_DATA_DIR naming a pack, health reports that pack and the engine's
    readers point into it."""
    tree = make_tree(tmp_path / "pack", pack=GOOD)
    code = (
        "import json, api.server as s, trajectory_sim.airspace as a;"
        "print(json.dumps({'health': s.health(), 'data': str(s._DATA), 'sectors': str(a._SECTORS_DIR)}))"
    )
    env = {**os.environ, "ATC_DATA_DIR": str(tree), "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=REPO, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["health"]["data_version"] == "2026.10.01.2"
    assert got["health"]["data_source"] == "pack"
    assert Path(got["data"]) == tree
    assert Path(got["sectors"]) == tree / "sectors_corrected"


def test_health_reports_the_bundled_data_by_default():
    import api.server as server

    info = server.health()
    assert info["data_source"] == datapaths.active().source
    assert info["data_version"] == datapaths.active().version
