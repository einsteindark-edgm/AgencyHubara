"""DashboardKit — canal 1: push al dashboard vía bus in-process (SSE).

Distinto de ``eventkit`` (canal 2): este NO es orquestación durable cross-worker.
Es un fan-out **in-process** del proceso API (un solo uvicorn) hacia las
conexiones SSE abiertas del dashboard. Los routers de cada plugin publican un
``DashboardEvent`` (envelope JSON-safe con ``domain`` opaco) y el endpoint
SSE multiplexado (``/api/dashboard/events``, hosteado por el plugin chats) lo
reparte. Contrato F1 de la auditoría frontend (push, no poll).

Uso canónico (en el router/api de un plugin)::

    from src.sdk.dashboardkit import get_dashboard_event_bus

    get_dashboard_event_bus().publish("orders", "order_updated", id=order_id)

Diferencias duras con eventkit (no confundir los canales):
- **Efímero, no durable**: si no hay suscriptores, el evento se pierde; con la
  cola llena se descarta el MÁS VIEJO. El frontend reconcilia con un poll lento
  de fallback. NO uses esto para coordinación entre workers (eso es eventkit).
- **In-process a propósito**: solo publica quien corre en el proceso uvicorn de
  la API. Los workers Temporal viven en otros contenedores y NO publican acá —
  sus mutaciones llegan por el diff del vault compartido. Si la API escalara a
  multi-proceso (``workers=N``), este bus necesitaría backend compartido
  (Redis pub/sub) — decisión consciente documentada en ``src/platform/events``.

La implementación privada vive en ``src.platform.events`` (platform). Esta
fachada solo re-exporta (regla 3 de ``src/sdk/CLAUDE.md``): los plugins importan
de acá, nunca de platform directo (el ratchet P-28 lo enforcea).
"""
from __future__ import annotations

from src.platform.events import (
    DashboardEvent as DashboardEvent,
    DashboardEventBus as DashboardEventBus,
    get_dashboard_event_bus as get_dashboard_event_bus,
)
