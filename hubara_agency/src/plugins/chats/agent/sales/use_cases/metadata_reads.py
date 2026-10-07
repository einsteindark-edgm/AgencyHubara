"""Leer `metadata.json` reintentando un error PASAJERO, sin frenar el bucle.

El store (`FilesystemMetadataStore.read`) hace UN intento y, ante un error
pasajero del sistema, lanza: nunca devuelve una copia vieja ni `{}`. El ingest
de WhatsApp corre en segundo plano DESPUÉS de responder 200 al webhook, así
que Meta no lo reintenta: un EMFILE de un milisegundo dejaba al cliente sin
respuesta. Acá se reintenta, con espera ASÍNCRONA (PR #393, séptima revisión).
"""
from __future__ import annotations

import asyncio
import errno
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

#: Los errores del sistema que se reintentan: no dicen nada del documento.
TRANSIENT_READ_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN, errno.ESTALE, errno.EIO})
#: La espera antes de cada reintento: hasta 3 reintentos, ~0,6 s en total.
READ_RETRY_DELAYS_S: tuple[float, ...] = (0.05, 0.15, 0.4)


class _Reads(Protocol):
    def read(self, session_id: str) -> dict[str, Any]: ...


async def read_retrying_transient_errors(store: _Reads, session_id: str) -> dict[str, Any]:
    """`store.read(session_id)`. Ante un error pasajero espera
    (`asyncio.sleep`, nunca `time.sleep`: el bucle sigue atendiendo) y
    reintenta, hasta 3 veces. Otro error, o el pasajero después del último
    reintento, se lanza."""
    for attempt, delay in enumerate(READ_RETRY_DELAYS_S, start=1):
        try:
            return store.read(session_id)
        except OSError as exc:
            if exc.errno not in TRANSIENT_READ_ERRNOS:
                raise
            logger.warning("metadata_read_retry", session_id=session_id, attempt=attempt, error=repr(exc)[:200])
        await asyncio.sleep(delay)
    return store.read(session_id)
