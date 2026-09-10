"""D1.9 — notificación de estado del pedido cuando Meta Business Agent responde.

Con MBA al frente, un mensaje de Hubara por Cloud API toma el hilo (§0.5 del
roadmap) y MBA queda mudo hasta el ``release``. Así que el ETA NO envía: le
cuenta la novedad a MBA por ``agent_event`` (contrato del plugin ``mba``,
``POST /api/mba/sessions/{session_key}/agent-events``) con el texto EXACTO
que habría enviado, y MBA se la transmite al cliente con sus palabras.

Por qué HTTP y no un import: ``eta`` no puede importar ``mba`` (P-3); el
endpoint aplica las guardas (flag, lista cerrada, quién controla, dedupe) y
registra el evento en la sesión. Identidad de servicio
(``HUBARA_SERVICE_TOKEN``), base ``HUBARA_API_BASE_URL`` (nombre del
servicio de la compose; el gate ``castkit_loopback`` prohíbe el loopback).
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from src.sdk import castkit

__all__ = ["api_base_url", "event_type_for_stage", "notify_via_mba"]

#: Por debajo del ``start_to_close_timeout`` (30 s) de la activity de claim
#: del ETA, y por encima del peor caso del adapter de mba (≈13,5 s): un
#: timeout acá es "el API no respondió", no "la activity venció con el POST
#: en vuelo". Sin heartbeat (R-HEARTBEAT: < 30 s).
_TIMEOUT_S = 15.0


def api_base_url() -> str:
    return os.environ.get("HUBARA_API_BASE_URL", "http://hubara-api:8000").rstrip("/")


async def notify_via_mba(
    session_id: str, *, event_type: str, order_id: str, message: str, payload: dict[str, Any] | None
) -> dict[str, Any]:
    """``POST /api/mba/sessions/{session_id}/agent-events``. Devuelve el
    outcome del plugin mba (``emitted`` / ``reason`` / ``agent_event_id``);
    levanta ``HTTPException`` (castkit) si el provider rechaza o no responde."""
    path = f"/api/mba/sessions/{quote(session_id, safe='')}/agent-events"
    return await castkit.forward(
        None,  # sin request entrante: identidad de servicio
        "POST",
        path,
        base_url=api_base_url(),
        timeout=_TIMEOUT_S,
        cast_label="eta.notify→mba.agent-events",
        body={"type": event_type, "order_id": order_id, "message": message, "payload": payload, "source": "eta"},
        auth="service",
    )


#: Etapa del pedido → tipo de evento del catálogo del plugin mba
#: (``src.plugins.mba.domain.agent_events.AGENT_EVENT_TYPES``; el endpoint
#: rechaza con 422 lo que no esté ahí — guard de deriva en los tests).
#: ``preparing`` distingue si el pago está realmente confirmado.
_STAGE_TYPES = {
    "ready": "order_ready",
    "shipping": "order_shipped",
    "delivered": "order_delivered",
    "cancelled": "order_cancelled",
}


def event_type_for_stage(stage: str, *, payment_confirmed: bool) -> str | None:
    if stage == "preparing":
        return "payment_received" if payment_confirmed else "order_preparing"
    return _STAGE_TYPES.get(stage)
