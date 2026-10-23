"""The engine's resource meter: /api/resources and the load test."""

from __future__ import annotations

import time

import api.server as server
from api.resources import LoadTest, Sampler
from tests.test_session_token import call


def test_resources_reports_this_machine_and_the_engine():
    status, body = call("GET", "/api/resources")
    assert status == 200
    assert body["machine"]["cores"] >= 1
    assert body["machine"]["ram_total_gb"] > 0
    assert body["engine"]["rss_gb"] > 0  # this very process
    assert body["engine"]["workers_configured"] >= 1
    assert isinstance(body["history"], list) and body["history"]
    assert body["cache"]["cap"] == server._EXPORT_CACHE_MAX
    assert body["load_test"]["state"] in ("idle", "running", "done", "failed")


def test_resources_is_behind_the_token_in_local_mode(monkeypatch):
    monkeypatch.setattr(server, "_SESSION_TOKEN", "tok")
    assert call("GET", "/api/resources")[0] == 401
    assert call("POST", "/api/loadtest", {"Authorization": "Bearer tok"})[0] != 401


def test_a_load_test_needs_flights_to_replay():
    server._EXPORT_CACHE.clear()
    status, body = call("POST", "/api/loadtest")
    assert status == 409
    assert "Generate some flights first" in body["detail"]


def test_sampler_keeps_history_and_extremes():
    s = Sampler()
    first = s.sample()
    s.sample()
    assert len(s.history) == 2
    assert first["rss_gb"] > 0
    assert s.idle_rss <= s.peak_rss
    assert s.cpu_seconds() >= 0


def test_load_test_counts_every_replayed_flight_and_discards_them():
    sampler = Sampler()
    sampler.sample()
    test = LoadTest(sampler)
    specs = [server.GenerateRequest(callsign=f"T{i}") for i in range(10)]
    built: list[int] = []
    discarded: list[str] = []

    def run_batch(batch):
        built.extend(sp.flight_index for sp in batch)
        return [("ok", ({"flight_key": f"k{sp.flight_index}"}, None)) for sp in batch]

    test.start(users=2, runs=2, specs=specs, run_batch=run_batch, discard=discarded.extend)
    for _ in range(100):
        if test.status()["state"] != "running":
            break
        time.sleep(0.05)
    st = test.status()
    assert st["state"] == "done", st
    assert st["summary"]["flights"] == 2 * 2 * 10
    assert len(st["done"]) == 4 and all(r["failed"] == 0 for r in st["done"])
    # Every replayed flight got a key of its own and was forgotten again.
    assert len(set(built)) == 40
    assert len(discarded) == 40
    assert all(i >= LoadTest.INDEX_BASE for i in built)
    # A second test may start once the first is done.
    test.start(users=1, runs=1, specs=specs, run_batch=run_batch, discard=discarded.extend)
