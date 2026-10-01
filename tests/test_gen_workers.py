"""How many generation workers the engine starts when nobody tells it.

The pool must fit the machine: a worker per spare core is right on a desktop
with memory to spare and fatal on a 512 MB container, where the kernel still
reports the host's cores.
"""

from __future__ import annotations

import pytest

import api.server as server


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("ATC_GEN_WORKERS", raising=False)
    server._auto_workers.cache_clear()
    yield
    server._auto_workers.cache_clear()


def auto(monkeypatch, cores, ram_mb):
    monkeypatch.setattr(server.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(server, "_available_ram_mb", lambda: ram_mb)
    server._auto_workers.cache_clear()
    return server._gen_workers()


def test_plenty_of_memory_is_bounded_by_cores(monkeypatch):
    assert auto(monkeypatch, 8, 16000) == 7
    assert auto(monkeypatch, 32, 64000) == 8  # never more than 8


def test_little_memory_wins_over_many_cores(monkeypatch):
    # 512 MB container on a big host: serial, whatever the core count says.
    assert auto(monkeypatch, 16, 390) == 1
    # 1.6 GB free on an 8 GB laptop: two workers, not seven.
    assert auto(monkeypatch, 8, 1600) == 2


def test_unknown_memory_falls_back_to_cores(monkeypatch):
    assert auto(monkeypatch, 4, None) == 3
    assert auto(monkeypatch, 1, None) == 1


def test_explicit_setting_overrides_everything(monkeypatch):
    monkeypatch.setattr(server, "_available_ram_mb", lambda: 100)
    monkeypatch.setenv("ATC_GEN_WORKERS", "5")
    assert server._gen_workers() == 5
    monkeypatch.setenv("ATC_GEN_WORKERS", "0")
    assert server._gen_workers() == 1


def test_available_ram_is_readable_here():
    ram = server._available_ram_mb()
    assert ram is None or ram > 0
