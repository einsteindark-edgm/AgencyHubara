"""Event bus — fan-out a múltiples sinks con aislamiento de errores.

API:

    bus = get_event_bus()
    await bus.record(event)         # fire-and-forget contra todos los sinks
    await bus.record_many(events)   # batch

Por qué hay un bus y no expones los sinks directo: queremos que un sink
fallando (rate limit de Meta CAPI, disco lleno) no rompa la lógica de
negocio que emitió el evento. El bus catchea por sink, loguea, sigue.

Construcción: el composition root del worker o del HTTP layer arma el
bus con los sinks que correspondan al deployment. Para tests, se inyecta
un bus con InMemorySink.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Iterable

import structlog

from src.platform.analytics.events import AnalyticsEvent
from src.platform.analytics.port import AnalyticsSink

logger = structlog.get_logger()


class EventBus:
    def __init__(self, sinks: list[AnalyticsSink]) -> None:
        self._sinks = sinks

    @property
    def sinks(self) -> list[AnalyticsSink]:
        return list(self._sinks)

    def add_sink(self, sink: AnalyticsSink) -> None:
        """Permite registrar sinks post-construcción (útil para tests y
        para integrar Meta CAPI tras conocer credenciales del tenant)."""
        self._sinks.append(sink)

    async def record(self, event: AnalyticsEvent) -> None:
        """Despacha el evento a todos los sinks en paralelo. Cada sink se
        envuelve en try/except para que un fallo no impacte a los demás."""
        if not self._sinks:
            return
        await asyncio.gather(
            *(self._safe_write(s, event) for s in self._sinks),
            return_exceptions=False,
        )

    async def record_many(self, events: Iterable[AnalyticsEvent]) -> None:
        for ev in events:
            await self.record(ev)

    async def _safe_write(self, sink: AnalyticsSink, event: AnalyticsEvent) -> None:
        try:
            await sink.write(event)
        except Exception as e:  # noqa: BLE001 — bus debe absorber
            logger.warning(
                "analytics_sink_failed",
                sink=getattr(sink, "name", type(sink).__name__),
                event_kind=event.kind,
                error=str(e),
            )


# =============================================================================
# Module-level singleton (R-STATELESS exception por design — el bus es
# inmutable salvo `add_sink` y vive durante todo el proceso del worker)
# =============================================================================


@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    """Singleton del bus por proceso.

    El composition root del worker / HTTP layer lo extiende vía
    `bus.add_sink(...)`. Si el deployment no agrega sinks, el bus es
    no-op (los `record()` son safe pero no escriben nada).
    """
    return EventBus(sinks=[])
