# Profiles the engine's per-flight cost in-process, on real rows from a CSV.
#   python prof_engine.py <csv> <n_flights> [serial|parallel]
# serial   -> ATC_GEN_WORKERS=1, cProfile over _run_batch (per-flight breakdown)
# parallel -> pool as the server would use it, wall time only (throughput)
import os, sys, csv, json, time, cProfile, pstats, io
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
mode = sys.argv[3] if len(sys.argv) > 3 else "serial"
os.environ["ATC_GEN_WORKERS"] = "1" if mode == "serial" else os.environ.get("ATC_GEN_WORKERS", "")
os.chdir(ROOT)
from api import server as S
from fastapi.encoders import jsonable_encoder

def main():
    rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")))[: int(sys.argv[2])]
    specs = []
    for i, r in enumerate(rows):
        specs.append(S.GenerateRequest(
            source="fpl", adep=r["adep"], ades=r["ades"], actype=r["actype"] or "B738",
            route=r["route"], callsign=r["callsign"], eobt=r["eobt"], gs_kt=float(r.get("gs") or 450),
            rfl=int(float(r["rfl"] or 330)), flight_index=i,
            sid=r.get("sid") or None, sid_runway=r.get("dep_rwy") or None,
            star=r.get("star") or None, star_runway=r.get("arr_rwy") or None,
            approach=r.get("approach") or None,
        ))

    # warm-up: first flight loads navdata/procedures
    t0 = time.perf_counter(); S._run_batch(specs[:1]); warm = time.perf_counter() - t0
    print(f"warm-up (first flight incl. navdata load): {warm:.2f} s")

    if mode == "serial":
        pr = cProfile.Profile()
        t0 = time.perf_counter(); pr.enable()
        out = S._run_batch(specs)
        pr.disable(); dt = time.perf_counter() - t0
    else:
        t0 = time.perf_counter()
        out = S._run_batch(specs)
        dt = time.perf_counter() - t0
        print(f"workers: {S._gen_workers()}")

    ok = [v for st, v in out if st == "ok"]
    errs = [v for st, v in out if st != "ok"]
    print(f"{len(ok)} ok, {len(errs)} errors, {dt:.2f} s total, {1000*dt/max(1,len(ok)):.1f} ms/flight (wall)")
    if errs: print("first errors:", errs[:3])

    # payload size and points
    t0 = time.perf_counter()
    import psutil
    me = psutil.Process(); rss = me.memory_info().rss + sum(c.memory_info().rss for c in me.children(recursive=True))
    print(f"memory after generation: parent {me.memory_info().rss/1e9:.2f} GB, parent+workers {rss/1e9:.2f} GB; export cache entries {len(S._EXPORT_CACHE)} (cap {S._EXPORT_CACHE_MAX})")
    ok = ok[:300]  # size stats on a sample; encoding 10k flights would need gigabytes
    enc = [jsonable_encoder(p) for p, _b in ok]
    t_enc = time.perf_counter() - t0
    t0 = time.perf_counter()
    js = json.dumps({"results": enc})
    t_dump = time.perf_counter() - t0
    p0 = enc[0]
    keys = list(p0.keys())
    def npts(p):
        for k in ("points", "trajectory", "samples", "track"):
            if isinstance(p.get(k), list): return k, len(p[k])
        best = max(((k, len(v)) for k, v in p.items() if isinstance(v, list)), key=lambda kv: kv[1], default=("?", 0))
        return best
    k, n = npts(p0)
    pts = [npts(p)[1] for p in enc]
    print(f"payload keys: {keys}")
    print(f"points per flight (field '{k}'): first={n}, mean={sum(pts)/len(pts):.0f}, max={max(pts)}")
    if isinstance(p0.get(k), list) and p0[k]:
        print("one point:", json.dumps(p0[k][len(p0[k])//2])[:400])
    print(f"JSON: {len(js)/1e6:.2f} MB for {len(enc)} flights = {len(js)/len(enc)/1e3:.1f} KB/flight; encode {t_enc:.2f} s + dumps {t_dump:.2f} s")
    # export bundle size
    b = next((b for _p, b in ok if b), None)
    if b is None:
        e = next(iter(S._EXPORT_CACHE.values()), None)
        if e: print("export cache entry: gdf rows", len(e["gdf"]), "cols", list(e["gdf"].columns)[:12])
    else:
        print("bundle gdf rows", len(b["gdf"]))

    if mode == "serial":
        s = io.StringIO(); ps = pstats.Stats(pr, stream=s)
        ps.sort_stats("cumulative").print_stats(45)
        ps.sort_stats("tottime").print_stats(30)
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "prof_engine.txt"), "w", encoding="utf-8").write(s.getvalue())
        print("profile written to prof_engine.txt")

if __name__ == "__main__":
    main()
