"""Las fotos de cada cliente que la visión está leyendo (en este proceso).

La foto entra al bot cuando la visión termina (1,5 s si basta el texto de la
foto; hasta ~4,5 s si hay que compararla con las fotos del catálogo) y la
ráfaga del workflow se cierra tras 1,5 s de silencio: el «¿tienes esta?» que
el cliente escribe detrás de la foto llegaba solo, el bot contestaba sin la
foto y la foto entraba como otro turno (2026-09-30). El ingest retiene los
mensajes del cliente mientras se lee una foto suya (`wait`), con tope: si la
visión se cuelga, el mensaje sale igual.

En memoria: la foto se lee en el mismo proceso de la API que recibió el
webhook (un solo proceso en producción). Si el proceso se reinicia, no queda
nada que esperar y el mensaje sale al instante, como antes.
"""
from __future__ import annotations

import asyncio
import time


class PhotoReads:
    def __init__(self) -> None:
        self._reading: dict[str, int] = {}
        self._idle: dict[str, asyncio.Event] = {}

    def begin(self, session_id: str) -> None:
        """Empieza la lectura de una foto del cliente."""
        self._reading[session_id] = self._reading.get(session_id, 0) + 1
        self._idle.setdefault(session_id, asyncio.Event()).clear()

    def end(self, session_id: str) -> None:
        """Terminó (la foto ya entró al bot, o falló): si era la última, suelta a los que esperan."""
        left = self._reading.get(session_id, 0) - 1
        if left > 0:
            self._reading[session_id] = left
            return
        self._reading.pop(session_id, None)
        idle = self._idle.pop(session_id, None)
        if idle is not None:
            idle.set()

    def reading(self, session_id: str) -> bool:
        return session_id in self._reading

    async def wait(self, session_id: str, *, max_s: float) -> float:
        """Espera a que no quede ninguna foto del cliente leyéndose, a lo sumo
        `max_s` segundos. Devuelve cuánto esperó."""
        start = time.monotonic()
        while session_id in self._reading:
            remaining = max_s - (time.monotonic() - start)
            idle = self._idle.get(session_id)
            if idle is None or remaining <= 0:
                break
            try:
                await asyncio.wait_for(idle.wait(), timeout=remaining)
            except TimeoutError:
                break
        return time.monotonic() - start
