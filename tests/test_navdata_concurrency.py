"""Two requests reaching a cold engine at once.

The API answers on a thread pool, and the page asks for an airport's
procedure list and a procedure's legs together. The procedure index is built
lazily on first use; a lookup that arrives while it is being built must wait
for the complete index, not search a half-built one and report "not found"
(which is what the desktop app's first STAR lookup did after every launch).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from trajectory_sim.navdata import NavData, ProcedureType

DATA = Path(__file__).resolve().parent.parent / "web" / "public" / "data" / "aixm"


def fresh_navdata() -> NavData:
    return NavData(
        sid_source=DATA / "sid_waypoint.geojson",
        star_source=DATA / "star_waypoint.geojson",
        approach_source=DATA / "pbn_waypoint.geojson",
        ils_source=DATA / "ils_wp.geojson",
    )


def test_lookup_during_the_first_load_waits_for_the_whole_index(monkeypatch):
    nav = fresh_navdata()

    # Widen the window: each source takes a moment to read, as it does on a
    # slow disk. STAR is read after SID, so a lookup for a STAR that starts
    # while SID is still being read used to find nothing.
    real_read = NavData._read_proc_source
    reading = threading.Event()

    def slow_read(self, proc_type):
        reading.set()
        time.sleep(0.15)
        return real_read(self, proc_type)

    monkeypatch.setattr(NavData, "_read_proc_source", slow_read)

    errors: list[BaseException] = []

    def first():  # triggers the load
        try:
            nav.lookup_procedure("VTSP", "URGA1D", proc_type=ProcedureType.STAR)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    found = {}

    def second():  # arrives mid-load
        reading.wait(timeout=5)
        try:
            found["proc"] = nav.lookup_procedure("VTSP", "URGA1D", proc_type=ProcedureType.STAR)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    a = threading.Thread(target=first)
    b = threading.Thread(target=second)
    a.start()
    b.start()
    a.join(timeout=30)
    b.join(timeout=30)

    assert not errors, f"a concurrent lookup failed: {errors[0]!r}"
    assert found["proc"].waypoints(), "the mid-load lookup got an empty procedure"


def test_many_simultaneous_first_lookups_all_succeed():
    nav = fresh_navdata()
    start = threading.Barrier(8)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker():
        start.wait(timeout=10)
        try:
            results.append(nav.lookup_procedure("VTSP", "URGA1D", proc_type=ProcedureType.STAR))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"{len(errors)} of 8 simultaneous lookups failed: {errors[0]!r}"
    assert len(results) == 8
