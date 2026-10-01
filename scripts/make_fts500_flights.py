"""Generate a fresh, importable day of Bangkok-FIR traffic (500+ flights).

Writes a PLAN file — the ``callsign,actype,adep,ades,eobt,rfl,gs,dep_rwy,
arr_rwy,sid,star,approach,route`` table that the generator panel's
**Import file** tab reads (``web/lib/flightFile.ts`` → ``parseCsv``), one row
per flight, one plan tab per row, ready for **Generate all**.

Nothing is invented. Each flight is assembled from the project's own data:

  * **Demand** — category mix (domestic / arrival / departure / overflight),
    hourly EOBT curve, city-pair market, operator mix per pair and the cruise
    level spread all come from ``scripts/data/thai24h_profile.json``, which is
    distilled from the real CAT062 surveillance log for 2025-12-23.
  * **Route** — a domestic pair files its published AIP route
    (``aip_routes_VT.json``, RNAV first, as the panel's picker orders it);
    international and overflight pairs file the gateway-to-gateway route that
    pair already flies in ``fts_traffic_20260709Star.csv``. A pair with neither
    is not drawn.
  * **Aircraft** — a type the operator is seen flying on that pair, else in its
    fleet, else a stage-length pool; always one with a Thai APM performance
    table, so the engine flies it on its own model.
  * **Runways / procedures** — the measured default runway for that aerodrome
    in the simulated month (``runway_default.csv``), the SID/STAR the backend's
    ``suggest_procedure`` picks for the route at that runway, and an approach
    only where the runway publishes exactly one (``lib/procedureLink.ts``).
  * **Level** — CAB semicircular (odd east / even west, great-circle ADEP→ADES
    track), capped at the airframe's reachable ceiling.
  * **Speed** — a filed cruise ground speed typical of the type (the
    departure-separation check compares filed speeds, and the panel would
    otherwise send its 450 kt default for an ATR too).

Every candidate row is then flown through the real ``/api/generate`` handler;
failures are dropped and replaced, so the file is known to generate.

Output: dummy_data/fts_500_flights_20260927.csv

Run:  python scripts/make_fts500_flights.py [--flights 520] [--seed 27]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from make_thai24h_flights import (  # noqa: E402
    AIRPORTS,
    PROFILE,
    bearing,
    foreign_ll,
    pick_actype,
    stage_nm,
    weighted,
    _level_window,
)
from trajectory_sim.performance import reachable_ceiling_ft  # noqa: E402
from trajectory_sim.validation import cab_cruising_level_capped  # noqa: E402

DAY = datetime(2026, 9, 27, tzinfo=timezone.utc)
OUT = _ROOT / "dummy_data" / "fts_500_flights_20260927.csv"
TEMPLATES = _ROOT / "dummy_data" / "fts_traffic_20260709Star.csv"
ROUTES = _ROOT / "web" / "public" / "data" / "aip_routes_VT.json"
RUNWAYS = _ROOT / "web" / "public" / "data" / "airports" / "runway_default.csv"
PERF = _ROOT / "trajectory_sim" / "data" / "thaiapm_performance.csv"

FIELDS = [
    "callsign", "actype", "adep", "ades", "eobt", "rfl", "gs",
    "dep_rwy", "arr_rwy", "sid", "star", "approach", "route",
]
_CATS = ("domestic", "arrival", "departure", "overflight")

_TURBOPROP = {"AT75", "AT76", "ATR72", "DH8D", "SF34", "C295", "CN35", "B350",
              "BE20", "C130"}
_WIDEBODY = {"A332", "A333", "A338", "A339", "A343", "A346", "A359", "A35K",
             "A388", "B744", "B748", "B763", "B772", "B773", "B77L", "B77W",
             "B788", "B789", "B78X", "C17"}

#: Typical cruise ground speed (kt) by type class — what gets filed in Item 15.
_GS_BY_TYPE = {
    "AT75": 265, "AT76": 275, "SF34": 270, "DH8D": 330, "C295": 250,
    "CN35": 240, "B350": 290, "BE20": 270, "C130": 290,
    "E135": 410, "E190": 435, "E290": 445, "SU95": 440,
    "A388": 490, "B744": 490, "B748": 495, "C17": 450,
}


def is_thai(icao: str) -> bool:
    return icao.startswith("VT")


def category(adep: str, ades: str) -> str:
    if is_thai(adep) and is_thai(ades):
        return "domestic"
    if is_thai(ades):
        return "arrival"
    if is_thai(adep):
        return "departure"
    return "overflight"


def filed_gs(actype: str, rng: random.Random) -> int:
    base = _GS_BY_TYPE.get(actype)
    if base is None:
        base = 485 if actype in _WIDEBODY else 450
    return int(round((base + rng.uniform(-10, 10)) / 5) * 5)


# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
def load_templates() -> list[dict]:
    with TEMPLATES.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def aip_routes() -> dict[tuple[str, str], str]:
    raw = json.loads(ROUTES.read_text(encoding="utf-8"))["routes"]
    out: dict[tuple[str, str], str] = {}
    for r in sorted(raw, key=lambda r: not r.get("rnav")):
        out.setdefault((r["adep"], r["ades"]), r["route"])
    return out


def runway_defaults(month: int) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    with RUNWAYS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["is_default"].strip().lower() != "t":
                continue
            if int(row["month_of_year"]) != month:
                continue
            out[(row["airport"].strip().upper(), row["direction"].strip().upper())] = (
                f"RW{row['runway'].strip().upper()}"
            )
    return out


def perf_types() -> set[str]:
    with PERF.open(encoding="utf-8") as f:
        return {r["actype"] for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Backend (called in-process, exactly the handlers the web app hits)
# ---------------------------------------------------------------------------
class Backend:
    def __init__(self) -> None:
        from fastapi import HTTPException, Response  # noqa: PLC0415

        from api import server  # noqa: PLC0415

        self.s = server
        self.HTTPException = HTTPException
        self.Response = Response
        self._suggest: dict[tuple, str] = {}
        self._approaches: dict[str, list[str]] = {}

    def suggest(self, airport: str, kind: str, route: str, runway: str) -> str:
        key = (airport, kind, route, runway)
        if key not in self._suggest:
            try:
                got = self.s.suggest_procedure(
                    airport, self.Response(), type=kind, route=route,
                    runway=runway or None,
                )
                self._suggest[key] = str(got.get("name") or "")
            except Exception:  # noqa: BLE001 — unknown field / no procedures
                self._suggest[key] = ""
        return self._suggest[key]

    def sole_approach(self, airport: str, runway: str) -> str:
        if not runway:
            return ""
        if airport not in self._approaches:
            try:
                got = self.s.list_procedures(airport, self.Response(), type="APPROACH")
                self._approaches[airport] = list(got.get("APPROACH") or [])
            except Exception:  # noqa: BLE001
                self._approaches[airport] = []
        digits = runway[2:]
        served = [
            n for n in self._approaches[airport]
            if re.match(rf"^R{re.escape(digits)}(-|$)", n.upper())
        ]
        return served[0] if len(served) == 1 else ""

    def generate(self, r: dict) -> str | None:
        """None on success, else the error text."""
        spec = {
            "source": "fpl",
            "callsign": r["callsign"],
            "actype": r["actype"],
            "adep": r["adep"],
            "ades": r["ades"],
            "route": r["route"],
            "eobt": r["eobt"].replace("Z", ""),
            "rfl": int(r["rfl"]),
            "gs_kt": float(r["gs"]),
        }
        for key, field in (
            ("sid", "sid"), ("star", "star"), ("approach", "approach"),
            ("sid_runway", "dep_rwy"), ("star_runway", "arr_rwy"),
        ):
            if r[field]:
                spec[key] = r[field]
        try:
            self.s.generate(self.s.GenerateRequest(**spec))
            return None
        except self.HTTPException as e:
            return str(e.detail)[:140]
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"[:140]


# ---------------------------------------------------------------------------
# Flight builder
# ---------------------------------------------------------------------------
class Builder:
    def __init__(self, rng: random.Random, backend: Backend) -> None:
        self.rng = rng
        self.b = backend
        self.perf = perf_types()
        self.aip = aip_routes()
        self.rwy = runway_defaults(DAY.month)

        tmpl = load_templates()
        self.pair_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.fleet: dict[str, Counter] = defaultdict(Counter)
        self.flight_nos: dict[str, list[str]] = defaultdict(list)
        for t in tmpl:
            self.pair_rows[(t["adep"], t["ades"])].append(t)
            m = re.match(r"^([A-Z]{3})(\d+)$", t["callsign"])
            if m:
                self.fleet[m.group(1)][t["actype"]] += 1
                self.flight_nos[m.group(1)].append(m.group(2))

        # Real demand per category, restricted to pairs we can actually file.
        self.pairs: dict[str, list[list]] = {}
        for cat in _CATS:
            usable = []
            for p, n in PROFILE["pairs"][cat]:
                adep, ades = p.split("-")
                if adep != ades and self.route_for(adep, ades):
                    usable.append([(adep, ades), n])
            self.pairs[cat] = usable
        self.used_callsigns: set[str] = set()

    # --- pieces -----------------------------------------------------------
    def route_for(self, adep: str, ades: str) -> str | None:
        if (adep, ades) in self.aip:
            return self.aip[(adep, ades)]
        rows = self.pair_rows.get((adep, ades))
        if rows:
            return Counter(r["route"] for r in rows).most_common(1)[0][0]
        return None

    def pick_airline(self, cat: str, adep: str, ades: str) -> str:
        pa = PROFILE["pair_airlines"].get(f"{adep}-{ades}")
        if pa:
            return weighted(pa, self.rng)
        seen = [r["callsign"][:3] for r in self.pair_rows.get((adep, ades), [])
                if re.match(r"^[A-Z]{3}\d", r["callsign"])]
        if seen:
            return self.rng.choice(seen)
        return weighted(PROFILE["airlines"][cat], self.rng)

    def _fits_stage(self, actype: str, nm: float, cat: str) -> bool:
        if actype in _TURBOPROP:
            return cat == "domestic" and (not nm or nm < 500)
        if actype in _WIDEBODY:
            return not nm or nm > 350
        return True

    def pick_actype(self, cat: str, airline: str, adep: str, ades: str) -> str:
        nm = stage_nm(adep, ades)
        on_pair = [r["actype"] for r in self.pair_rows.get((adep, ades), [])
                   if r["callsign"].startswith(airline) and r["actype"] in self.perf]
        if on_pair:
            return self.rng.choice(on_pair)
        fleet = [(t, n) for t, n in self.fleet.get(airline, Counter()).items()
                 if t in self.perf and self._fits_stage(t, nm, cat)]
        if fleet:
            return weighted([list(x) for x in fleet], self.rng)
        # An operator with no fleet on record flies what the pair really sees.
        any_on_pair = [r["actype"] for r in self.pair_rows.get((adep, ades), [])
                       if r["actype"] in self.perf]
        if any_on_pair:
            return self.rng.choice(any_on_pair)
        for _ in range(10):
            t = pick_actype(cat, adep, ades, self.rng)
            if t in self.perf:
                return t
        return "A320"

    def pick_callsign(self, airline: str) -> str:
        nos = self.flight_nos.get(airline) or ["100", "250", "620"]
        for _ in range(200):
            width = len(self.rng.choice(nos))
            lo = 10 ** (width - 1) if width > 1 else 1
            n = self.rng.randint(lo, 10 ** width - 1)
            cs = f"{airline}{n}"
            if cs not in self.used_callsigns:
                self.used_callsigns.add(cs)
                return cs
        raise RuntimeError(f"ran out of callsigns for {airline}")

    def pick_eobt(self, cat: str) -> str:
        hour = weighted([[h, n] for h, n in enumerate(PROFILE["hourly_utc"][cat])],
                        self.rng)
        minute = self.rng.randrange(0, 60, 5)
        t = DAY + timedelta(hours=int(hour), minutes=minute)
        return t.strftime("%Y-%m-%dT%H:%M:00Z")

    def _ll(self, icao: str) -> tuple[float, float] | None:
        return AIRPORTS[icao][:2] if icao in AIRPORTS else foreign_ll(icao)

    def pick_level(self, cat: str, adep: str, ades: str, actype: str) -> int:
        a, b = self._ll(adep), self._ll(ades)
        brg = bearing(a[0], a[1], b[0], b[1]) if a and b else 90
        nm = stage_nm(adep, ades)
        # Prefer the levels this pair is really filed at; else the real day's
        # category histogram inside the band this stage supports.
        filed = [int(r["rfl"]) for r in self.pair_rows.get((adep, ades), [])
                 if r["rfl"].isdigit() and int(r["rfl"]) >= 60]
        if filed:
            desired = self.rng.choice(filed) + self.rng.choice((-20, 0, 0, 20))
        else:
            lo, hi = _level_window(cat, nm)
            band = [[k, v] for k, v in PROFILE["cruise_fl"][cat] if lo <= int(k) <= hi]
            desired = int(weighted(band, self.rng)) if band else (lo + hi) // 2
        return cab_cruising_level_capped(
            brg, desired, int(reachable_ceiling_ft(actype) // 100)
        )

    def procedures(self, adep: str, ades: str, route: str) -> dict:
        dep_rwy = sid = arr_rwy = star = ""
        if is_thai(adep):
            dep_rwy = self.rwy.get((adep, "DEP")) or self.rwy.get((adep, "ALL"), "")
            sid = self.b.suggest(adep, "SID", route, dep_rwy)
            if not sid and dep_rwy:
                sid = self.b.suggest(adep, "SID", route, "")
                if sid:
                    dep_rwy = ""
        if is_thai(ades):
            arr_rwy = self.rwy.get((ades, "ARR")) or self.rwy.get((ades, "ALL"), "")
            star = self.b.suggest(ades, "STAR", route, arr_rwy)
            if not star and arr_rwy:
                star = self.b.suggest(ades, "STAR", route, "")
                if star:
                    arr_rwy = ""
        return {
            "dep_rwy": dep_rwy, "sid": sid, "arr_rwy": arr_rwy, "star": star,
            "approach": self.b.sole_approach(ades, arr_rwy) if is_thai(ades) else "",
        }

    # --- one flight -------------------------------------------------------
    def build(self, cat: str) -> dict:
        adep, ades = weighted(self.pairs[cat], self.rng)
        route = self.route_for(adep, ades)
        airline = self.pick_airline(cat, adep, ades)
        actype = self.pick_actype(cat, airline, adep, ades)
        return {
            "callsign": self.pick_callsign(airline),
            "actype": actype,
            "adep": adep,
            "ades": ades,
            "eobt": self.pick_eobt(cat),
            "rfl": self.pick_level(cat, adep, ades, actype),
            "gs": filed_gs(actype, self.rng),
            "route": route,
            **self.procedures(adep, ades, route),
        }


def category_quota(total: int) -> dict[str, int]:
    counts = PROFILE["category_counts"]
    s = sum(counts[c] for c in _CATS)
    q = {c: round(total * counts[c] / s) for c in _CATS}
    q["domestic"] += total - sum(q.values())
    return q


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--flights", type=int, default=520)
    ap.add_argument("--seed", type=int, default=27)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    backend = Backend()
    builder = Builder(rng, backend)

    rows: list[dict] = []
    failures: list[str] = []
    for cat, want in category_quota(args.flights).items():
        got = attempts = 0
        while got < want:
            attempts += 1
            if attempts > want * 4:
                raise SystemExit(f"{cat}: too many failures ({len(failures)})")
            r = builder.build(cat)
            err = backend.generate(r)
            if err:
                builder.used_callsigns.discard(r["callsign"])
                failures.append(f"{cat:10s} {r['callsign']} {r['adep']}->{r['ades']}: {err}")
                continue
            rows.append(r)
            got += 1
            if len(rows) % 50 == 0:
                print(f"  {len(rows)} flights verified...", flush=True)

    rows.sort(key=lambda r: (r["eobt"], r["callsign"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r[k] for k in FIELDS} for r in rows)

    cats = Counter(category(r["adep"], r["ades"]) for r in rows)
    print(f"Flights:    {len(rows)}  {dict(cats)}")
    print(f"Pairs:      {len({(r['adep'], r['ades']) for r in rows})}")
    print(f"Operators:  {len({r['callsign'][:3] for r in rows})}")
    print(f"Types:      {len({r['actype'] for r in rows})}")
    print(f"SID: {sum(1 for r in rows if r['sid'])}  STAR: {sum(1 for r in rows if r['star'])}"
          f"  Approach: {sum(1 for r in rows if r['approach'])}")
    print(f"Rejected by /api/generate and replaced: {len(failures)}")
    for line in failures[:15]:
        print(f"  FAIL {line}")
    print(f"CSV:        {OUT}")


if __name__ == "__main__":
    main()
