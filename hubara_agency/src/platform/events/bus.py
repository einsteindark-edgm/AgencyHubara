"""Bus de eventos del dashboard (in-process, single-publisher fan-out).

Contrato F1 de la auditoría frontend (2026-06-10): el dashboard deja de
pollear y pasa a push — UN stream SSE multiplexado por dominio
(`/api/dashboard/events`, hosteado por el plugin chats) al que los routers
de los plugins publican vía este bus.

Diseño:
  - **In-process a propósito.** `run_api.py` corre uvicorn en UN proceso
    (sin `workers=N`); los workers Temporal viven en OTROS contenedores y NO
    publican acá — sus mutaciones llegan por el diff del vault compartido
    (ver sampler en `chats/api/dashboard.py`). Si algún día la API escala a
    multi-proceso, este bus necesita backend compartido (Redis pub/sub) —
    decisión consciente, no accidente.
  - **`publish()` es sync y nunca bloquea**: con la cola de un suscriptor
    llena se descarta el evento MÁS VIEJO (el frontend tiene fallback poll
    lento que reconcilia a un consumidor que se atrasó).
  - R-DIP: platform NO importa plugins — el bus es genérico; `domain` es un
    string opaco que los plugins eligen ("chats" | "orders" | "eta" |
    "catalog").
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

_QUEUE_MAXSIZE = 100


@dataclass(frozen=True)
class DashboardEvent:
    """Envelope que viaja por el SSE. `payload` opcional (ej. snapshot chats)."""

    domain: str
    type: str
    id: str | None = None
    payload: Any | None = None
    ts_ms: int = 0

    def to_sse(self) -> str:
        body: dict[str, Any] = {
            "domain": self.domain,
            "type": self.type,
            "id": self.id,
            "ts_ms": self.ts_ms,
        }
        if self.payload is not None:
            body["payload"] = self.payload
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


class DashboardEventBus:
    """Fan-out mínimo: N suscriptores (uno por conexión SSE), colas acotadas."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[DashboardEvent]] = set()

    def subscribe(self) -> asyncio.Queue[DashboardEvent]:
        queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[DashboardEvent]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(
        self,
        domain: str,
        event_type: str,
        *,
        id: str | None = None,
        payload: Any | None = None,
    ) -> DashboardEvent:
        event = DashboardEvent(
            domain=domain,
            type=event_type,
            id=id,
            payload=payload,
            ts_ms=int(time.time() * 1000),
        )
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Consumidor lento: descartamos lo más viejo para no bloquear
                # jamás al publisher. El fallback poll del frontend reconcilia.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return event


_bus: DashboardEventBus | None = None


def get_dashboard_event_bus() -> DashboardEventBus:
    """Singleton del proceso API (un solo proceso uvicorn — ver módulo doc)."""
    global _bus
    if _bus is None:
        _bus = DashboardEventBus()
    return _bus
