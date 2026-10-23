"""Measure what the engine really uses - memory, CPU, time - under a load you
choose, to size a server.

    python scripts/resource_meter.py                         # 1 user, 1,000 flights, once
    python scripts/resource_meter.py --users 3 --runs 2      # 3 people at once, 2 runs each
    python scripts/resource_meter.py --flights 2000 --csv "dummy_data/2000 flight.csv"

Needs a running engine (``npm run dev:api`` in web/, ``scripts\\serve_lan.ps1``,
or the frozen ``atc-engine``) and the ``psutil`` package in the venv. The
meter attaches to every engine process it finds (the server and its worker
pool), samples them twice a second, sends the same requests the page sends
("Generate all": chunks of 40 flights, up to 3 chunks in flight per user),
and writes:

  results/meter_<time>.csv      one row per sample (engine RSS, CPU, system RAM ...)
  results/meter_<time>.txt      the summary printed at the end

The summary gives the numbers a server is sized from: idle memory, peak
memory, memory kept after the runs (the result cache), CPU-seconds per
flight, and the time each user waited. It also converts them into the
cloud bill the measured footprint would cause and the cores needed for a
target time.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    sys.exit("psutil is missing: .venv\\Scripts\\python -m pip install psutil")

ROOT = Path(__file__).resolve().parent.parent
GB = 2**30


# --------------------------------------------------------------- the load
def load_specs(path: Path, n: int) -> list[dict]:
    """The first ``n`` flights of a CSV, as the page would send them
    (cycled if the file is shorter)."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [{k.strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]
    if not rows:
        sys.exit(f"no flights in {path}")
    specs = []
    for i in range(n):
        r = rows[i % len(rows)]
        s = {
            "source": "fpl",
            "vtsp_to_vtbs": r.get("adep", "").upper() == "VTSP",
            "adep": r.get("adep", "").upper(),
            "ades": r.get("ades", "").upper(),
            "actype": r.get("actype", "B738").upper(),
            "route": r.get("route", ""),
            "callsign": (r.get("callsign") or "FLT").upper(),
            "eobt": r.get("eobt", "2026-01-03T08:15:00").replace("Z", ""),
            "gs_kt": float(r.get("gs") or r.get("gs_kt") or 450),
            "rfl": int(float(r.get("rfl") or 330)),
            "output_every_s": 5,
        }
        for src, dst in (("sid", "sid"), ("star", "star"), ("approach", "approach"),
                         ("dep_rwy", "sid_runway"), ("arr_rwy", "star_runway")):
            if r.get(src):
                s[dst] = r[src].upper()
        specs.append(s)
    return specs


def post(url: str, body: dict, timeout: float = 900) -> tuple[int, int, float]:
    """POST JSON; returns (status, bytes received, seconds)."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = len(r.read())
            return r.status, n, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, 0, time.perf_counter() - t0


def one_user(api: str, specs: list[dict], chunk: int, parallel: int, user: int, run: int, log: list) -> dict:
    """One "Generate all" the way the page does it: the first chunk alone,
    then up to ``parallel`` chunks at a time. Returns the run's figures."""
    chunks = [specs[i:i + chunk] for i in range(0, len(specs), chunk)]
    t0 = time.perf_counter()
    ok = bytes_in = fails = 0

    def send(i: int) -> None:
        nonlocal ok, bytes_in, fails
        status, n, _ = post(f"{api}/api/generate_batch", {"flights": chunks[i], "index_offset": i * chunk})
        if status == 200:
            ok += len(chunks[i]); bytes_in += n
        else:
            fails += len(chunks[i])

    send(0)
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        list(ex.map(send, range(1, len(chunks))))
    wall = time.perf_counter() - t0
    row = {"user": user, "run": run, "flights": ok, "failed": fails, "seconds": round(wall, 1), "mb_received": round(bytes_in / 2**20, 1)}
    log.append(row)
    print(f"  user {user} run {run}: {ok} flights in {wall:.1f} s, {row['mb_received']} MB received" + (f", {fails} FAILED" if fails else ""), flush=True)
    return row


# ----------------------------------------------------------------- the meter
def engine_processes() -> list[psutil.Process]:
    """Every process that is the engine: a uvicorn/python running
    api.server, or the frozen atc-engine, plus all their children."""
    roots = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if name.startswith("atc-engine") or ("api.server" in cmd and "resource_meter" not in cmd):
                roots.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    seen = {}
    for r in roots:
        for p in [r, *r.children(recursive=True)]:
            seen[p.pid] = p
    # Children of a root that is itself a child: keep only one tree.
    return list(seen.values())


class Sampler(threading.Thread):
    def __init__(self, interval: float):
        super().__init__(daemon=True)
        self.interval = interval
        self.rows: list[dict] = []
        self.phase = "idle"
        self.stop = threading.Event()
        self.cpu_at_start: dict[int, float] = {}
        self.cpu_seen: dict[int, float] = {}

    def run(self) -> None:
        procs: dict[int, psutil.Process] = {}
        while not self.stop.is_set():
            t = time.time()
            current = engine_processes()
            for p in current:
                if p.pid not in procs:
                    procs[p.pid] = p
                    try:
                        p.cpu_percent(None)  # prime
                    except psutil.Error:
                        pass
            rss = cpu = 0.0
            alive = 0
            # Sample through the handles kept from earlier ticks: cpu_percent
            # measures since *that handle's* last call, so a fresh handle
            # every tick would always read 0.
            for p in (procs[q.pid] for q in current):
                try:
                    rss += p.memory_info().rss
                    cpu += p.cpu_percent(None)
                    ct = p.cpu_times()
                    self.cpu_seen[p.pid] = ct.user + ct.system
                    alive += 1
                except psutil.Error:
                    pass
            vm = psutil.virtual_memory()
            self.rows.append({
                "t": round(t, 1), "phase": self.phase, "engine_procs": alive,
                "engine_rss_gb": round(rss / GB, 3), "engine_cpu_pct": round(cpu, 1),
                "system_used_gb": round(vm.used / GB, 2), "system_cpu_pct": psutil.cpu_percent(None),
            })
            time.sleep(self.interval)

    def cpu_seconds_since(self, mark: dict[int, float]) -> float:
        return sum(v - mark.get(pid, 0.0) for pid, v in self.cpu_seen.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--csv", default=str(ROOT / "dummy_data" / "fts_500_flights_20260927.csv"))
    ap.add_argument("--flights", type=int, default=1000, help="flights per run")
    ap.add_argument("--users", type=int, default=1, help="people running at the same time")
    ap.add_argument("--runs", type=int, default=1, help="runs per user, one after another")
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--parallel", type=int, default=3, help="chunks in flight per user (the page uses 3)")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=10, help="seconds to watch after the load")
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()

    try:
        with urllib.request.urlopen(f"{args.api}/api/health", timeout=5) as r:
            health = json.load(r)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"no engine at {args.api} ({e}); start it first (npm run dev:api, or scripts\\serve_lan.ps1)")
    procs = engine_processes()
    if not procs:
        sys.exit("the engine answers but no engine process was found on this machine; the meter must run on the server")
    print(f"engine {health.get('version')} at {args.api}, {len(procs)} process(es) found; "
          f"machine: {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads, {psutil.virtual_memory().total / GB:.0f} GB RAM")

    specs = load_specs(Path(args.csv), args.flights)
    sampler = Sampler(args.interval)
    sampler.start()
    print("idle baseline (5 s) ...")
    time.sleep(5)
    idle_rows = list(sampler.rows)

    sampler.phase = "load"
    mark = dict(sampler.cpu_seen)
    print(f"load: {args.users} user(s) x {args.runs} run(s) x {args.flights} flights ...")
    runs: list[dict] = []
    t_load = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.users) as ex:
        futures = []
        for u in range(1, args.users + 1):
            def user_job(u=u):
                for r in range(1, args.runs + 1):
                    one_user(args.api, specs, args.chunk, args.parallel, u, r, runs)
            futures.append(ex.submit(user_job))
        for f in futures:
            f.result()
    load_seconds = time.perf_counter() - t_load
    cpu_seconds = sampler.cpu_seconds_since(mark)

    sampler.phase = "after"
    print(f"settling ({args.settle:.0f} s) ...")
    time.sleep(args.settle)
    sampler.stop.set()
    sampler.join(timeout=5)

    rows = sampler.rows
    idle_rss = statistics.mean(r["engine_rss_gb"] for r in idle_rows) if idle_rows else 0
    load_rows = [r for r in rows if r["phase"] == "load"]
    after_rows = [r for r in rows if r["phase"] == "after"]
    peak_rss = max(r["engine_rss_gb"] for r in rows)
    peak_procs = max(r["engine_procs"] for r in rows)
    after_rss = statistics.mean(r["engine_rss_gb"] for r in after_rows[-6:]) if after_rows else peak_rss
    peak_sys = max(r["system_used_gb"] for r in rows)
    avg_cpu = statistics.mean(r["engine_cpu_pct"] for r in load_rows) if load_rows else 0
    flights_done = sum(r["flights"] for r in runs)
    cpu_per_flight = cpu_seconds / flights_done if flights_done else 0
    waits = [r["seconds"] for r in runs]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with (out / f"meter_{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    cores = psutil.cpu_count(logical=False) or 1
    lines = [
        f"RESOURCE METER  {stamp}   engine {health.get('version')}   {args.users} user(s) x {args.runs} run(s) x {args.flights} flights   chunk {args.chunk}, {args.parallel} in flight",
        f"machine: {cores} cores / {psutil.cpu_count()} threads, {psutil.virtual_memory().total / GB:.0f} GB RAM",
        "",
        "MEMORY (engine: server + workers)",
        f"  idle before the load      {idle_rss:5.2f} GB",
        f"  peak during the load      {peak_rss:5.2f} GB   ({peak_procs} processes)",
        f"  kept after the load       {after_rss:5.2f} GB   (results cache + workers; what an idle server holds between runs)",
        f"  whole machine, peak used  {peak_sys:5.2f} GB",
        "",
        "CPU",
        f"  engine CPU-seconds for {flights_done} flights: {cpu_seconds:,.0f}  ->  {cpu_per_flight:.3f} CPU-s per flight",
        f"  engine CPU during the load: {avg_cpu:.0f} % on average (100 % = one core)",
        "",
        "TIME",
        f"  all runs finished in {load_seconds:.1f} s; per run: " + ", ".join(f"{s:.1f} s" for s in waits)
        + (f"; mean {statistics.mean(waits):.1f} s, worst {max(waits):.1f} s" if len(waits) > 1 else ""),
        f"  throughput {flights_done / load_seconds:,.1f} flights/s while loaded",
        "",
        "SIZING FROM THESE NUMBERS",
        f"  RAM to provision: peak {peak_rss:.1f} GB + 1 GB headroom + OS  ->  {max(4, round(peak_rss + 2)):.0f} GB minimum, {max(8, round(2 * peak_rss + 2)):.0f} GB comfortable",
        f"  cloud memory bill at US$10/GB-month for the idle footprint kept between runs: ~US${after_rss * 10:.0f}/month"
        f" (US${idle_rss * 10:.0f} if idle workers are shut down)",
        f"  cores for {args.flights} flights in 60 s on cores like these: {cpu_per_flight * args.flights / 60:.1f};"
        f" in 30 s: {cpu_per_flight * args.flights / 30:.1f}  (cloud vCPUs are ~0.5-0.6x of a desktop core: multiply by 1.7-2)",
        f"  {args.users} user(s) at once: each waited {statistics.mean(waits):.0f} s; with N users on the same cores expect about N x the single-user time",
        "",
        f"samples: {out / f'meter_{stamp}.csv'}",
    ]
    text = "\n".join(lines)
    (out / f"meter_{stamp}.txt").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    return 1 if any(r["failed"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
