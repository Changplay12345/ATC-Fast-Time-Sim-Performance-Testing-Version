"""The engine's own resource meter, for sizing a server from inside the app.

Two parts, both used by ``api.server``:

* :class:`Sampler` — a thread that every two seconds adds up the memory and
  CPU of this process and its worker processes, keeps ten minutes of
  history, and remembers the idle and peak figures. ``GET /api/resources``
  returns its snapshot.
* :class:`LoadTest` — replays flights the engine has already built, as N
  users each running the "Generate all" pattern (chunks of 40, three in
  flight), and reports the wait per run, CPU-seconds per flight and memory
  before, at peak and after. ``POST /api/loadtest`` starts one; the status
  rides along in ``GET /api/resources``.

The numbers are the ones a server is chosen by, so they are measured here,
on the machine that would be that server, rather than estimated.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import psutil

GB = 2**30
SAMPLE_EVERY_S = 2.0
HISTORY_S = 600


class Sampler:
    """Memory and CPU of this process and its children, sampled on a thread."""

    def __init__(self) -> None:
        self._me = psutil.Process(os.getpid())
        self._handles: dict[int, psutil.Process] = {}
        self._cpu_seen: dict[int, float] = {}
        self._lock = threading.Lock()
        self.history: deque[dict] = deque(maxlen=int(HISTORY_S / SAMPLE_EVERY_S))
        self.idle_rss = None  # the smallest footprint seen
        self.peak_rss = 0.0
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.sample()  # one point straight away, so the first request has data
        threading.Thread(target=self._loop, name="resource-meter", daemon=True).start()

    def _loop(self) -> None:
        while True:
            time.sleep(SAMPLE_EVERY_S)
            try:
                self.sample()
            except Exception:  # noqa: BLE001 - the meter must never take the engine down
                pass

    def _tree(self) -> list[psutil.Process]:
        try:
            procs = [self._me, *self._me.children(recursive=True)]
        except psutil.Error:
            procs = [self._me]
        out = []
        for p in procs:
            # One handle per PID: cpu_percent measures since *that handle's*
            # previous call, so a fresh handle would always read 0.
            h = self._handles.get(p.pid)
            if h is None:
                h = self._handles[p.pid] = p
                try:
                    h.cpu_percent(None)
                except psutil.Error:
                    pass
            out.append(h)
        for pid in list(self._handles):
            if pid not in {p.pid for p in procs}:
                del self._handles[pid]
        return out

    def sample(self) -> dict:
        rss = cpu = 0.0
        alive = 0
        for p in self._tree():
            try:
                rss += p.memory_info().rss
                cpu += p.cpu_percent(None)
                t = p.cpu_times()
                self._cpu_seen[p.pid] = t.user + t.system
                alive += 1
            except psutil.Error:
                pass
        row = {"t": round(time.time(), 1), "rss_gb": round(rss / GB, 3), "cpu_pct": round(cpu, 1), "procs": alive}
        with self._lock:
            self.history.append(row)
            self.peak_rss = max(self.peak_rss, rss)
            self.idle_rss = rss if self.idle_rss is None else min(self.idle_rss, rss)
        return row

    def cpu_seconds(self) -> float:
        """Total CPU time of every process seen so far (workers included)."""
        return sum(self._cpu_seen.values())

    def snapshot(self) -> dict:
        if not self.history:
            # Asked before the thread's first tick (or the thread was never
            # started, as in the tests): measure now rather than report zeros.
            self.sample()
        with self._lock:
            history = list(self.history)
            last = history[-1] if history else {"rss_gb": 0, "cpu_pct": 0, "procs": 1}
            idle = self.idle_rss or 0
            peak = self.peak_rss
        vm = psutil.virtual_memory()
        return {
            "machine": {
                "cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
                "threads": psutil.cpu_count() or 1,
                "ram_total_gb": round(vm.total / GB, 1),
                "ram_used_gb": round(vm.used / GB, 1),
                "cpu_pct": psutil.cpu_percent(None),
            },
            "engine": {
                "rss_gb": last["rss_gb"],
                "cpu_pct": last["cpu_pct"],
                "processes": last["procs"],
                "idle_rss_gb": round(idle / GB, 3),
                "peak_rss_gb": round(peak / GB, 3),
                "cpu_seconds_total": round(self.cpu_seconds(), 1),
            },
            "history": history,
            "sample_every_s": SAMPLE_EVERY_S,
        }


class LoadTest:
    """N simulated users replaying real flights through the batch path."""

    CHUNK = 40
    IN_FLIGHT = 3
    # Replayed flights get their own index range so their keys never collide
    # with the user's real flights.
    INDEX_BASE = 10_000_000

    def __init__(self, sampler: Sampler) -> None:
        self._sampler = sampler
        self._lock = threading.Lock()
        self._state: dict = {"state": "idle"}

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def running(self) -> bool:
        return self.status().get("state") == "running"

    def start(
        self,
        users: int,
        runs: int,
        specs: list,
        run_batch: Callable[[list], list],
        discard: Callable[[list[str]], None],
    ) -> None:
        """Begin in the background; ``status()`` follows it. ``specs`` are
        the flights to replay (cycled), ``run_batch`` builds a chunk the way
        ``/api/generate_batch`` does, ``discard`` forgets the results."""
        with self._lock:
            if self._state.get("state") == "running":
                raise RuntimeError("a load test is already running")
            self._state = {
                "state": "running", "users": users, "runs": runs, "flights_per_run": len(specs),
                "started_at": time.time(), "done": [], "progress": {str(u): 0 for u in range(1, users + 1)},
            }
        threading.Thread(target=self._run, args=(users, runs, specs, run_batch, discard), daemon=True).start()

    def _run(self, users: int, runs: int, specs: list, run_batch, discard) -> None:
        s = self._sampler
        s.sample()
        rss_before = s.history[-1]["rss_gb"]
        cpu_before = s.cpu_seconds()
        t_start = time.time()
        results: list[dict] = []
        failed_any = []

        def one_user(user: int) -> None:
            for run in range(1, runs + 1):
                chunks = [specs[i:i + self.CHUNK] for i in range(0, len(specs), self.CHUNK)]
                ok = fails = 0
                t0 = time.perf_counter()

                def send(i: int) -> None:
                    nonlocal ok, fails
                    base = self.INDEX_BASE + (user * runs + run) * 100_000 + i * self.CHUNK
                    batch = [sp.model_copy(update={"flight_index": base + k}) for k, sp in enumerate(chunks[i])]
                    try:
                        outcomes = run_batch(batch)
                    except Exception as e:  # noqa: BLE001
                        fails += len(batch)
                        failed_any.append(str(e))
                        return
                    keys = []
                    for status, value in outcomes:
                        if status == "ok":
                            ok += 1
                            payload = value[0] if isinstance(value, tuple) else value
                            try:
                                keys.append(str(payload["flight_key"]))
                            except (KeyError, TypeError):
                                pass
                        else:
                            fails += 1
                    discard(keys)
                    with self._lock:
                        self._state["progress"][str(user)] += len(batch)

                send(0)
                with ThreadPoolExecutor(max_workers=self.IN_FLIGHT) as ex:
                    list(ex.map(send, range(1, len(chunks))))
                row = {"user": user, "run": run, "flights": ok, "failed": fails, "seconds": round(time.perf_counter() - t0, 1)}
                with self._lock:
                    self._state["done"].append(row)
                results.append(row)

        with ThreadPoolExecutor(max_workers=users) as ex:
            list(ex.map(one_user, range(1, users + 1)))

        wall = time.time() - t_start
        s.sample()
        rss_after = s.history[-1]["rss_gb"]
        window = [r for r in s.history if r["t"] >= t_start - SAMPLE_EVERY_S]
        peak = max((r["rss_gb"] for r in window), default=rss_after)
        flights = sum(r["flights"] for r in results)
        cpu = s.cpu_seconds() - cpu_before
        waits = [r["seconds"] for r in results] or [0.0]
        summary = {
            "flights": flights,
            "failed": sum(r["failed"] for r in results),
            "seconds_total": round(wall, 1),
            "seconds_mean": round(statistics.mean(waits), 1),
            "seconds_worst": round(max(waits), 1),
            "cpu_seconds": round(cpu, 1),
            "cpu_s_per_flight": round(cpu / flights, 4) if flights else None,
            "rss_before_gb": rss_before,
            "rss_peak_gb": peak,
            "rss_after_gb": rss_after,
            "flights_per_s": round(flights / wall, 1) if wall else None,
        }
        with self._lock:
            self._state.update({
                "state": "failed" if failed_any and not flights else "done",
                "finished_at": time.time(),
                "summary": summary,
                "error": failed_any[0] if failed_any else None,
            })
