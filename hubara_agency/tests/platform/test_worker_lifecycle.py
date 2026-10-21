"""Apagado ordenado de los workers (auditoría de Temporal, 2026-10-15).

Un deploy hace `docker stop`: SIGTERM y, pasado el `stop_grace_period`, SIGKILL.
Sin manejar SIGTERM, el worker moría a mitad de sus activities (un envío de
WhatsApp, un turno del LLM) y Temporal se enteraba recién al vencer sus
timeouts. Con `run_worker`, SIGTERM deja de pedir tareas y espera (hasta
`graceful_shutdown_timeout`) a las que están en curso.
"""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from src.platform.temporal.worker_lifecycle import WORKER_GRACEFUL_SHUTDOWN, run_worker


@pytest.fixture(autouse=True)
def _signals_never_kill_pytest():
    """Red de seguridad: si `run_worker` no maneja la señal, la ignora el
    proceso de pytest (el test falla por timeout en vez de matar la corrida)."""
    previous = {sig: signal.signal(sig, lambda *_: None) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


class FakeWorker:
    """Lo que `run_worker` usa de un `temporalio.worker.Worker`: `run()` corre
    hasta que `shutdown()` lo apaga."""

    def __init__(self, *, on_run=None, fails: bool = False) -> None:
        self.on_run = on_run
        self.fails = fails
        self.shutdowns = 0
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        if self.on_run is not None:
            self.on_run()
        if self.fails:
            raise RuntimeError("el worker se cayó")
        await self._stopped.wait()

    async def shutdown(self) -> None:
        self.shutdowns += 1
        self._stopped.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_a_stop_signal_shuts_the_worker_down_in_order(sig: signal.Signals) -> None:
    # El deploy: `docker stop` le manda la señal al proceso mientras el worker corre.
    worker = FakeWorker(on_run=lambda: os.kill(os.getpid(), sig))

    await asyncio.wait_for(run_worker(worker), timeout=5)

    assert worker.shutdowns == 1


@pytest.mark.asyncio
async def test_two_workers_in_one_process_both_shut_down_on_one_signal() -> None:
    """`src.run_workers` corre varios workers en un proceso (dev local)."""
    first, second = FakeWorker(), FakeWorker()
    running = asyncio.gather(run_worker(first), run_worker(second))
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(running, timeout=5)

    assert (first.shutdowns, second.shutdowns) == (1, 1)


@pytest.mark.asyncio
async def test_a_worker_that_crashes_still_raises() -> None:
    with pytest.raises(RuntimeError, match="se cayó"):
        await asyncio.wait_for(run_worker(FakeWorker(fails=True)), timeout=5)


@pytest.mark.asyncio
async def test_after_the_worker_stops_the_signal_is_no_longer_handled() -> None:
    """El handler no queda colgado del loop: una señal posterior no apaga un
    worker que ya terminó (ni revienta)."""
    worker = FakeWorker(on_run=lambda: os.kill(os.getpid(), signal.SIGTERM))
    await asyncio.wait_for(run_worker(worker), timeout=5)

    assert asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM) is False


def test_the_grace_period_lets_a_whatsapp_send_finish_and_fits_the_docker_stop() -> None:
    """Un envío o un turno corto termina; el `stop_grace_period` del compose de
    prod (`tests/infra/test_prod_compose_graceful_stop.py`) es mayor."""
    assert 20 <= WORKER_GRACEFUL_SHUTDOWN.total_seconds() <= 45


def test_the_sdk_runtime_reexports_the_worker_lifecycle() -> None:
    """Los workers lo importan del SDK (P-28), el mismo objeto que platform."""
    import src.platform.temporal.worker_lifecycle as impl
    import src.sdk.runtime as kit

    assert getattr(kit, "run_worker", None) is impl.run_worker
    assert getattr(kit, "WORKER_GRACEFUL_SHUTDOWN", None) is impl.WORKER_GRACEFUL_SHUTDOWN


@pytest.mark.asyncio
async def test_a_second_signal_exits_at_once_instead_of_waiting_for_the_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """En dev, Ctrl-C mientras el worker espera a sus activities: el segundo
    Ctrl-C sale ya (antes quedaba esperando hasta el graceful timeout)."""
    import src.platform.temporal.worker_lifecycle as lifecycle

    forced: list[signal.Signals] = []
    draining = asyncio.Event()

    class SlowDrain(FakeWorker):
        async def shutdown(self) -> None:
            self.shutdowns += 1
            draining.set()
            await asyncio.sleep(3600)  # activities que no terminan

    worker = SlowDrain(on_run=lambda: os.kill(os.getpid(), signal.SIGINT))

    def force(sig: signal.Signals) -> None:
        forced.append(sig)
        worker._stopped.set()

    monkeypatch.setattr(lifecycle, "_force_exit", force)
    monkeypatch.setattr(lifecycle, "_REPEAT_WINDOW_S", 0.1)
    running = asyncio.create_task(run_worker(worker))
    await asyncio.wait_for(draining.wait(), timeout=5)
    await asyncio.sleep(0.3)  # la persona aprieta Ctrl-C otra vez
    os.kill(os.getpid(), signal.SIGINT)
    await asyncio.sleep(0.2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert forced == [signal.SIGINT]


@pytest.mark.asyncio
async def test_the_copy_of_the_same_ctrl_c_that_uv_forwards_is_not_a_second_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`uv run` le reenvía el SIGINT al hijo además del que manda la terminal
    al grupo (medido 2026-10-16: un Ctrl-C = dos SIGINT): esa copia casi
    inmediata no es «otra vez Ctrl-C» y no corta el drenado."""
    import src.platform.temporal.worker_lifecycle as lifecycle

    forced: list[signal.Signals] = []
    draining = asyncio.Event()

    class SlowDrain(FakeWorker):
        async def shutdown(self) -> None:
            self.shutdowns += 1
            draining.set()
            await asyncio.sleep(0.6)  # las activities terminan
            self._stopped.set()

    def ctrl_c_through_uv() -> None:
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(lifecycle, "_force_exit", forced.append)
    worker = SlowDrain(on_run=ctrl_c_through_uv)
    await asyncio.wait_for(run_worker(worker), timeout=5)

    assert forced == [], "una copia del mismo Ctrl-C cortó el drenado"
    assert worker.shutdowns == 1
