"""Rate limiter in-memory (sliding window) — SEC-13.

Defensa en profundidad contra flooding de la API. In-memory por proceso (no
distribuido): suficiente para el deploy single/pocas-réplicas; para escala
horizontal migrar a un store compartido (Redis). Opt-in: `max_requests <= 0`
lo desactiva (default en dev).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Cuenta requests por clave en una ventana deslizante.

    `clock` es inyectable (default `time.monotonic`) para tests deterministas.
    `max_requests <= 0` desactiva el límite (siempre permite).
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """True si `key` está bajo el límite (y registra el hit); False si lo excede."""
        if self._max <= 0:
            return True
        now = self._clock()
        cutoff = now - self._window
        hits = self._hits[key]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True


def client_ip(request) -> str:  # noqa: ANN001 — starlette Request
    """IP del cliente real. Detrás de Caddy/CloudFront el peer es el proxy, así
    que preferimos el primer hop de `X-Forwarded-For` (el cliente original)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install_rate_limit_middleware(app, limiter, is_exempt=lambda _p: False):  # noqa: ANN001
    """Cuelga un middleware HTTP que aplica `limiter` por IP de cliente.

    `is_exempt(path)` → True saltea el límite (ej. el webhook de Meta). Si el
    limiter está desactivado (`max_requests <= 0`) el overhead es un `allow`
    que retorna True al toque.
    """
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _rate_limit(request, call_next):  # noqa: ANN001, ANN202
        if not is_exempt(request.url.path) and not limiter.allow(client_ip(request)):
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    return _rate_limit
