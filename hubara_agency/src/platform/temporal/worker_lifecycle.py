"""Apagado ordenado de los workers de Temporal (auditoría de Temporal, 2026-10-15).

Un deploy hace `docker stop`: SIGTERM y, pasado el `stop_grace_period` del
compose, SIGKILL. Python como PID 1 ignora SIGTERM (el compose de prod corre
con `init: true` para que le llegue) y, sin un handler, el worker moría a mitad
de sus activities (un envío de WhatsApp, un turno del LLM): Temporal se enteraba
recién al vencer sus timeouts.

`run_worker(worker)` corre el worker hasta SIGTERM/SIGINT y lo apaga en orden:
deja de pedir tareas y espera, hasta `graceful_shutdown_timeout`
(`WORKER_GRACEFUL_SHUTDOWN`, que cada worker le pasa a su `Worker(...)`), a las
activities en curso; pasado ese tiempo las cancela y Temporal las reintenta en
el worker nuevo. Varios workers en un proceso (`src.run_workers`) comparten la
misma señal: un SIGTERM apaga a todos.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
import weakref
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from loguru import logger

#: Lo que el worker espera a sus activities en curso antes de cancelarlas: un
#: envío o un turno corto terminan. Menor que el `stop_grace_period` del
#: compose de prod (si no, SIGKILL corta el apagado).
WORKER_GRACEFUL_SHUTDOWN = timedelta(seconds=30)

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)

# Un evento de apagado por event loop, con sus handlers y cuántos workers lo usan.
_stops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[asyncio.Event, list[int]]] = (
    weakref.WeakKeyDictionary()
)


def _acquire_stop(loop: asyncio.AbstractEventLoop) -> asyncio.Event:
    entry = _stops.get(loop)
    if entry is None:
        stop = asyncio.Event()

        def _request(sig: signal.Signals) -> None:
            if not stop.is_set():
                logger.info("{} recibida: apagando los workers en orden", sig.name)
            stop.set()

        for sig in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError):  # Windows
                loop.add_signal_handler(sig, _request, sig)
        entry = _stops[loop] = (stop, [0])
    entry[1][0] += 1
    return entry[0]


def _release_stop(loop: asyncio.AbstractEventLoop) -> None:
    entry = _stops.get(loop)
    if entry is None:
        return
    entry[1][0] -= 1
    if entry[1][0] <= 0:
        for sig in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        del _stops[loop]


@contextlib.asynccontextmanager
async def stop_signal() -> AsyncIterator[asyncio.Event]:
    """El evento que SIGTERM/SIGINT encienden en este loop, compartido con los
    `run_worker` del proceso (`src.run_workers` lo usa para saber que los
    workers se están apagando solos)."""
    loop = asyncio.get_running_loop()
    try:
        yield _acquire_stop(loop)
    finally:
        _release_stop(loop)


async def run_worker(worker: Any) -> None:
    """Corre `worker` (un `temporalio.worker.Worker`) hasta SIGTERM/SIGINT y lo
    apaga en orden. Si el worker se cae, la excepción sube."""
    loop = asyncio.get_running_loop()
    stop = _acquire_stop(loop)
    running = asyncio.create_task(worker.run())
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({running, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if not running.done():
            await worker.shutdown()
        await running
    except asyncio.CancelledError:
        running.cancel()
        raise
    finally:
        stopping.cancel()
        _release_stop(loop)
