"""Cache en proceso con TTL y tope de entradas.

Incidente 2026-09-25: la API de Ads memoizaba en un `dict` con TTL que solo se
miraba al leer. Con una key que cambiaba en cada request, cada request dejaba
una entrada nueva (~8.1 MB) que nadie borraba, hasta que la caja se quedó sin
RAM. Un cache de proceso tiene que acotarse a sí mismo: este saca las entradas
más viejas cuando pasa del tope.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class BoundedTTLCache(Generic[K, V]):
    """`get`/`put` con vencimiento a `ttl_s` segundos y a lo sumo `max_entries`.

    Seguro entre hilos (los endpoints sync de FastAPI corren en un threadpool).
    """

    def __init__(
        self,
        *,
        ttl_s: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        """El valor guardado, o None si no está o ya venció."""
        with self._lock:
            self._drop_expired(self._clock())
            hit = self._entries.get(key)
            return None if hit is None else hit[1]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            self._entries.pop(key, None)
            self._entries[key] = (now, value)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def _drop_expired(self, now: float) -> None:
        # Orden de escritura = orden de vencimiento (TTL único): basta con
        # sacar desde el frente hasta la primera entrada vigente.
        while self._entries:
            written_at = next(iter(self._entries.values()))[0]
            if now - written_at < self.ttl_s:
                return
            self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
