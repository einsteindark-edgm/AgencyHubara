"""Guard del router público del connector (DoD de seguridad de D1.2).

Meta llama ``/api/mba/tools/*`` desde IPs que no controlamos y sin más auth
que la API key. Dos límites baratos y deterministas:

- ``RateLimiter``: token bucket por clave (``<ip>:<tool>``), reloj inyectable
  (testeable sin dormir), memoria acotada (``max_keys``: se descarta la clave
  menos reciente).
- ``MAX_BODY_BYTES``: tope del body JSON (el request más grande legítimo, un
  ``register_order`` con 50 items, cabe holgado en 64 KiB).

Puro: sin FastAPI, sin threads. Es per-proceso (una API); si algún día hay
varias réplicas, el límite es por réplica — suficiente contra abuso, no es
cuota de facturación.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable

MAX_BODY_BYTES = 64 * 1024

#: ráfaga permitida por (IP, tool) y reposición por segundo. MBA llama una tool
#: por turno del cliente; 30 de golpe + 5/s cubre ráfagas reales con margen.
DEFAULT_CAPACITY = 30
DEFAULT_REFILL_PER_S = 5.0


class RateLimiter:
    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_s: float = DEFAULT_REFILL_PER_S,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._capacity = float(capacity)
        self._refill = float(refill_per_s)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key → (tokens, at)

    @property
    def size(self) -> int:
        return len(self._buckets)

    def _tokens(self, key: str, now: float) -> float:
        tokens, at = self._buckets.get(key, (self._capacity, now))
        return min(self._capacity, tokens + (now - at) * self._refill)

    def remaining(self, key: str) -> float:
        """Tokens disponibles ahora, sin consumir ni registrar la clave."""
        return self._tokens(key, self._clock())

    def allow(self, key: str) -> bool:
        now = self._clock()
        tokens = self._tokens(key, now)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return allowed
