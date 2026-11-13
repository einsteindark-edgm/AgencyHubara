"""Una línea por request HTTP, sin datos personales y con la memoria del proceso.

Memoria de la caja de app (2026-11-05): la API tuvo dos picos que casi tumban
la caja (2,3 GB el 26-oct, 2,6 GB el 3-nov) y no se supo qué pantalla los
disparó. Los logs de docker se borran al recrear el contenedor, y la línea de
uvicorn trae los tickets del SSE (`?ticket=`) y teléfonos en la ruta
(`/sessions/wa_57…`): no se puede guardar afuera tal cual.

Cada request deja un JSON con:

* `route`: la PLANTILLA de la ruta que atendió (`/api/dashboard/sessions/{session_id}`),
  nunca la ruta real; `None` si ninguna ruta la atendió (un 404 de un escáner).
* `query`: los nombres del query; de los valores, solo los cortos de dígitos o
  fechas (`days=56`, `start=2026-11-01`). El resto queda `…` (tickets, tokens,
  teléfonos: un teléfono tiene más de 10 dígitos).
* `status`, `ms`: el estado y cuánto tardó (un stream, hasta que terminó).
* `rss_mb`: la memoria residente del proceso al terminar (Linux; `None` fuera).
* `peak_mb`: el máximo que llegó a tener el proceso. El primer request tras el
  cual salta es el culpable de un pico.

En prod, `HUBARA_ACCESS_LOG_FILE` (lo fija el compose de la API) nombra el
archivo; rota a los 10 MB y el agente de CloudWatch lo guarda 14 días
(`infra/terraform/compute/modules/app-instance/monitoring.tf`). Sin archivo no
se escribe nada. Escribir nunca rompe un request.
"""
from __future__ import annotations

import json
import logging
import os
import re
import resource
import sys
import threading
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any
from urllib.parse import parse_qsl

from src.platform import config

Sink = Callable[[dict[str, Any]], None]
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: Un valor del query que se puede guardar: dígitos y guiones, hasta 10
#: (`56`, `2026-11-01`). Un teléfono (12 dígitos) o un ticket no entran.
_SAFE_VALUE = re.compile(r"[0-9-]{1,10}")
_SAFE_KEY = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_PAGE_BYTES = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 3
_logger = logging.getLogger("hubara.access")
_logger.propagate = False
_logger.setLevel(logging.INFO)
_handler_lock = threading.Lock()
_handler_path: str | None = None


def route_template(scope: Scope) -> str | None:
    """La plantilla de la ruta que atendió el request (la deja el router en el scope)."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


def safe_query(raw: bytes) -> str:
    parts = []
    for key, value in parse_qsl(raw.decode("latin-1"), keep_blank_values=True):
        name = key if _SAFE_KEY.fullmatch(key) else "…"
        parts.append(f"{name}={value if _SAFE_VALUE.fullmatch(value) else '…'}")
    return "&".join(parts)


def rss_mb() -> int | None:
    """Memoria residente del proceso ahora (Linux: `/proc/self/statm`)."""
    try:
        with open("/proc/self/statm", "rb") as f:
            pages = int(f.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return round(pages * _PAGE_BYTES / 2**20)


def peak_mb() -> int:
    """El máximo de memoria residente que tuvo el proceso (KB en Linux, bytes en macOS)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 2**20 if sys.platform == "darwin" else peak / 1024)


class AccessLogMiddleware:
    """ASGI puro (no `BaseHTTPMiddleware`): un stream (SSE) pasa sin
    envolverse y se registra una vez, cuando termina."""

    def __init__(self, app: ASGIApp, sink: Sink, clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self._sink = sink
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = self._clock()
        status = 500  # si el app revienta antes de responder

        async def send_and_watch(message: Message) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 500)
            await send(message)

        try:
            await self.app(scope, receive, send_and_watch)
        finally:
            self._emit(scope, status, started)

    def _emit(self, scope: Scope, status: int, started: float) -> None:
        try:
            self._sink({
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "method": scope.get("method"),
                "route": route_template(scope),
                "query": safe_query(scope.get("query_string") or b""),
                "status": status,
                "ms": max(0, round((self._clock() - started) * 1000)),
                "rss_mb": rss_mb(),
                "peak_mb": peak_mb(),
            })
        except Exception:  # noqa: BLE001 — la línea de acceso nunca rompe un request
            pass


def install_access_log(app: Any, sink: Sink) -> None:
    """Cuelga el middleware afuera de todo lo que ya tiene `app`."""
    app.add_middleware(AccessLogMiddleware, sink=sink)


def _file_logger(path: str) -> logging.Logger:
    global _handler_path
    with _handler_lock:
        if _handler_path != path:
            for old in list(_logger.handlers):
                _logger.removeHandler(old)
                old.close()
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
            _handler_path = path
    return _logger


def config_file_sink(line: dict[str, Any]) -> None:
    """Escribe la línea en `config.ACCESS_LOG_FILE` (leído en cada request:
    vacío = no se escribe)."""
    path = config.ACCESS_LOG_FILE
    if path:
        _file_logger(path).info(json.dumps(line, ensure_ascii=False, separators=(",", ":")))
