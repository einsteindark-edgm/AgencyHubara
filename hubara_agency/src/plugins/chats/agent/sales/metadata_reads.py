"""Leer `metadata.json` reintentando un error PASAJERO.

El store (`FilesystemMetadataStore.read`) hace UN intento y, ante un error
pasajero del sistema, lanza: nunca devuelve una copia vieja ni `{}`. Quien lee
al empezar algo que no puede fallar por un EMFILE de un milisegundo reintenta
acá (PR #393, séptima y octava revisión):

  * el ingest de WhatsApp y el router (`read_retrying_transient_errors`, con
    espera ASÍNCRONA: corren en el bucle; Meta no reintenta el ingest);
  * las activities y tools cuya lectura inicial el workflow no atrapa
    (`read_retrying_transient_errors_sync`, esperas cortas; los workflows no
    se tocan, L-9).

Módulo liviano a propósito (sin imports del paquete `use_cases`): lo importan
tools y activities sin arrastrar el ingest.
"""
from __future__ import annotations

import asyncio
import errno
import time
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

#: Los errores del sistema que se reintentan: no dicen nada del documento.
TRANSIENT_READ_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN, errno.ESTALE, errno.EIO})
#: La espera antes de cada reintento del ingest y del router: hasta 3
#: reintentos, ~0,6 s en total.
READ_RETRY_DELAYS_S: tuple[float, ...] = (0.05, 0.15, 0.4)
#: La espera de la variante síncrona (activities y tools): esperas cortas.
SYNC_READ_RETRY_DELAYS_S: tuple[float, ...] = (0.02, 0.06, 0.15)


class _Reads(Protocol):
    def read(self, session_id: str) -> dict[str, Any]: ...


def _retryable(exc: OSError, session_id: str, attempt: int) -> bool:
    if exc.errno not in TRANSIENT_READ_ERRNOS:
        return False
    logger.warning("metadata_read_retry", session_id=session_id, attempt=attempt, error=repr(exc)[:200])
    return True


async def read_retrying_transient_errors(store: _Reads, session_id: str) -> dict[str, Any]:
    """`store.read(session_id)`. Ante un error pasajero espera
    (`asyncio.sleep`, nunca `time.sleep`: el bucle sigue atendiendo) y
    reintenta, hasta 3 veces. Otro error, o el pasajero después del último
    reintento, se lanza."""
    for attempt, delay in enumerate(READ_RETRY_DELAYS_S, start=1):
        try:
            return store.read(session_id)
        except OSError as exc:
            if not _retryable(exc, session_id, attempt):
                raise
        await asyncio.sleep(delay)
    return store.read(session_id)


def read_retrying_transient_errors_sync(store: _Reads, session_id: str) -> dict[str, Any]:
    """Lo mismo para activities y tools: esperas cortas (20, 60 y 150 ms),
    solo ante un error pasajero; otro error, o el pasajero tras el último
    reintento, se lanza."""
    for attempt, delay in enumerate(SYNC_READ_RETRY_DELAYS_S, start=1):
        try:
            return store.read(session_id)
        except OSError as exc:
            if not _retryable(exc, session_id, attempt):
                raise
        time.sleep(delay)
    return store.read(session_id)
