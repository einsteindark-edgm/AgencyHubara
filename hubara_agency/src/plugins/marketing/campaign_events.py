"""Avance de campañas en vivo en el dashboard.

El envío corre en el worker de campañas, otro proceso que la API: escribe el
avance en `_campaigns/<id>.json` en cada lote y no llega al bus del dashboard
(in-process de la API). La API muestrea esos archivos (solo `stat`, sin
leerlos) y publica `marketing / campaign_updated` con el id de cada campaña
que cambió; el panel invalida la lista al recibirlo. Mismo camino que el
muestreo del vault de chats, con los archivos de este plugin.
"""
import asyncio
import contextlib
import logging
from collections.abc import Callable

from src.plugins.marketing.campaign_store import CampaignStore
from src.sdk.dashboardkit import DashboardEventBus, get_dashboard_event_bus

log = logging.getLogger(__name__)

DOMAIN = "marketing"
CAMPAIGN_UPDATED = "campaign_updated"

#: Un lote de 100 sale en ~10 s (10 msg/s): con 2 s el avance se ve casi al
#: instante y el costo es un `stat` por campaña.
SAMPLE_INTERVAL_S = 2.0


class CampaignEventsSampler:
    """Compara las firmas de los archivos de campañas entre muestras. La
    primera muestra es la línea base y no publica nada."""

    def __init__(self, store_factory: Callable[[], CampaignStore], bus: DashboardEventBus) -> None:
        self._store_factory = store_factory
        self._bus = bus
        self._prev: dict[str, tuple[int, int, int]] | None = None

    def tick(self) -> None:
        signatures = self.sample()
        if signatures is not None:
            self.publish_changes(signatures)

    def sample(self) -> dict[str, tuple[int, int, int]] | None:
        """Solo disco (corre en un thread). `None` si la muestra falló."""
        try:
            return self._store_factory().file_signatures()
        except Exception:  # noqa: BLE001 — una muestra mala no frena las siguientes
            log.exception("campañas en vivo: la muestra falló")
            return None

    def publish_changes(self, curr: dict[str, tuple[int, int, int]]) -> None:
        """Publica en el bus (en el event loop: las colas del bus no son
        thread-safe)."""
        prev, self._prev = self._prev, curr
        if prev is None:
            return
        for campaign_id in sorted(set(prev) | set(curr)):
            if prev.get(campaign_id) != curr.get(campaign_id):
                self._bus.publish(DOMAIN, CAMPAIGN_UPDATED, id=campaign_id)


_task: asyncio.Task | None = None


async def _loop(sampler: CampaignEventsSampler) -> None:
    while True:
        signatures = await asyncio.to_thread(sampler.sample)
        if signatures is not None:
            sampler.publish_changes(signatures)
        await asyncio.sleep(SAMPLE_INTERVAL_S)


def start_campaign_events(store_factory: Callable[[], CampaignStore]) -> None:
    """Arranca el muestreo si no corre (el arranque de la API puede llamarlo
    más de una vez)."""
    global _task
    if _task is None or _task.done():
        sampler = CampaignEventsSampler(store_factory, get_dashboard_event_bus())
        _task = asyncio.get_running_loop().create_task(_loop(sampler))


async def stop_campaign_events() -> None:
    global _task
    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
