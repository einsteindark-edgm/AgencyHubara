"""Apagado ordenado de los workers de Temporal (auditoría de Temporal, 2026-10-15).

Un deploy hace `docker stop`: SIGTERM y, pasado el `stop_grace_period` del
compose, SIGKILL. Python corre como PID 1, y el kernel no le entrega a PID 1 las
señales que no maneja: sin un handler, el SIGTERM se perdía y el worker moría
por SIGKILL a mitad de sus activities (un envío de WhatsApp, un turno del LLM);
Temporal se enteraba recién al vencer sus timeouts. Con el handler de acá, PID 1
sí la recibe (sin `init: true`, que duplicaba los procesos que mide CloudWatch).

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
import os
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
#: Una señal repetida antes de esto es la misma (la copia que reenvía `uv run`);
#: después, es la segunda y el proceso sale sin esperar el drenado.
_REPEAT_WINDOW_S = 1.0

# Un evento de apagado por event loop, con sus handlers y cuántos workers lo usan.
_stops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[asyncio.Event, list[int]]] = (
    weakref.WeakKeyDictionary()
)


def _acquire_stop(loop: asyncio.AbstractEventLoop) -> asyncio.Event:
    entry = _stops.get(loop)
    if entry is None:
        stop = asyncio.Event()

        first_at: list[float] = []

        def _request(sig: signal.Signals) -> None:
            if stop.is_set():
                # `uv run` le reenvía el SIGINT al hijo además del que manda
                # la terminal al grupo: un Ctrl-C llega dos veces casi juntas.
                # Esa copia no es «otra vez Ctrl-C».
                if loop.time() - first_at[0] < _REPEAT_WINDOW_S:
                    return
                _force_exit(sig)
                return
            first_at.append(loop.time())
            logger.info("{} recibida: apagando los workers en orden", sig.name)
            stop.set()

        for sig in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError):  # Windows
                loop.add_signal_handler(sig, _request, sig)
        entry = _stops[loop] = (stop, [0])
    entry[1][0] += 1
    return entry[0]


def _force_exit(sig: signal.Signals) -> None:
    """La segunda señal mientras los workers drenan: salida inmediata (en dev,
    el segundo Ctrl-C no espera el graceful timeout). En prod `docker stop`
    manda una sola SIGTERM; el SIGKILL del `stop_grace_period` hace lo mismo."""
    logger.warning("{} otra vez: salida inmediata, sin esperar a las activities", sig.name)
    os._exit(128 + int(sig))


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
