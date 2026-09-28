"""`BoundedTTLCache`: un cache de proceso que no crece sin límite.

Nació del incidente 2026-09-25 (la API de Ads retenía ~8.1 MB por request en
un dict con TTL que nunca borraba). El contrato: a lo sumo `max_entries`
entradas, y ninguna entrada vencida sobrevive a un acceso al cache.
"""
from __future__ import annotations

from src.sdk.runtime import BoundedTTLCache


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_writing_drops_entries_that_already_expired():
    clock = _Clock()
    cache = BoundedTTLCache(ttl_s=15, max_entries=16, clock=clock)
    cache.put("a", 1)
    cache.put("b", 2)

    clock.now = 16.0
    cache.put("c", 3)

    assert len(cache) == 1
    assert cache.get("c") == 3


def test_reading_drops_entries_that_already_expired():
    clock = _Clock()
    cache = BoundedTTLCache(ttl_s=15, max_entries=16, clock=clock)
    cache.put("a", 1)
    cache.put("b", 2)

    clock.now = 15.0

    assert cache.get("a") is None
    assert len(cache) == 0


def test_over_the_cap_the_oldest_write_goes_first():
    cache = BoundedTTLCache(ttl_s=15, max_entries=2, clock=_Clock())
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)  # reescribir renueva la entrada

    cache.put("c", 3)

    assert len(cache) == 2
    assert cache.get("b") is None
    assert (cache.get("a"), cache.get("c")) == (10, 3)
