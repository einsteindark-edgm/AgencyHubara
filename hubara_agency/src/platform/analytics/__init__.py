"""Analytics platform — tracking centralizado de toda interacción cliente.

Cubre los dominios:

* **Click analytics** — cada vez que el cliente toca un componente UI nuestro
  (button, list row, flow CTA, product card, cta_url, order pay), un evento
  `wa_interaction.<kind>` se persiste para correlación con campañas y
  optimización de UI.
* **Referral attribution** — cuando el cliente llega vía CTWA / FB post, el
  `referral` se envía a Meta Conversions API for Business Messaging para
  cerrar el círculo de atribución (campaña → mensaje → venta).
* **Conversion events** — `add_to_cart`, `initiate_checkout`, `purchase`,
  `lead`, etc. — para Advantage+ Shopping campaigns y DPA.

DEHA: vive en `platform/` porque es cross-plugin. Cualquier plugin emite
con `record_event(...)` y los sinks decidirán a dónde escribir. Implementa
el patrón puerto/adaptador puro — sin Temporal, sin requests directos.

Sinks soportados (configurable via env):
  1. `FilesystemAnalyticsSink` — JSONL en `WORKSPACE_VAULT_DIR/_analytics/`.
     Default siempre activo. Útil para post-mortem y debugging.
  2. `MetaConversionsAPISink` — POSTea a `graph.facebook.com/{pixel_id}/events`
     para attribution Meta. Activable por env. Requiere `META_PIXEL_ID` +
     `META_CAPI_ACCESS_TOKEN`.
  3. (Futuro) `PrometheusSink`, `PostgresSink`, etc.

Composition root en `composition.py` (sibling). Las activities y use cases
hacen `from src.platform.analytics import get_event_bus`.
"""
from src.platform.analytics.bus import EventBus, get_event_bus
from src.platform.analytics.events import (
    AnalyticsEvent,
    EventCategory,
    EventKind,
    make_referral_captured,
    make_wa_interaction,
    make_outbound_sent,
    make_conversion,
    make_delivery_status,
)
from src.platform.analytics.port import AnalyticsSink

__all__ = [
    "EventBus",
    "get_event_bus",
    "AnalyticsEvent",
    "EventCategory",
    "EventKind",
    "AnalyticsSink",
    "make_referral_captured",
    "make_wa_interaction",
    "make_outbound_sent",
    "make_conversion",
    "make_delivery_status",
]
