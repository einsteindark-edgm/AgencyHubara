"""Puerto de sinks analytics (DEHA: Protocol, no ABC).

Un sink consume eventos y los escribe a algún destino (filesystem, Meta
CAPI, DB, etc.). Es async porque la mayoría implican I/O. El bus invoca
todos los sinks en paralelo y captura excepciones — un sink fallando NO
debe bloquear al resto.
"""
from __future__ import annotations

from typing import Protocol

from src.platform.analytics.events import AnalyticsEvent


class AnalyticsSink(Protocol):
    """Sink async de eventos analytics."""

    name: str

    async def write(self, event: AnalyticsEvent) -> None:
        """Persiste un evento. Idempotencia esperada — si llega el mismo
        event_id dos veces, el sink decide deduplicar (o no, según costo)."""
        ...
