"""Helpers puros para la ventana de servicio de WhatsApp Cloud API.

WhatsApp permite enviar mensajes free-form al cliente sin costo SOLO dentro
de una "service window" de 24h que se abre con cada inbound del cliente. Si
el cliente llegó por Click-to-WhatsApp Ads (CTWA), hay además una ventana
extendida de 72h donde cualquier tipo de mensaje (incluso templates) es
gratis (`pricing.type=free_entry_point`).

Este módulo es 100% puro (sin I/O, sin Temporal, sin datetime.now()). El
caller pasa `now_ms` desde una activity (R-DET).

Ver HU-WA24H-001 §3.3 para el contrato completo.
"""
from __future__ import annotations

from typing import Any


# =============================================================================
# Constantes
# =============================================================================

#: Duración de la ventana de servicio (24 horas) en milisegundos.
SERVICE_WINDOW_MS: int = 24 * 60 * 60 * 1000

#: Duración de la ventana extendida CTWA (72 horas) en milisegundos.
CTWA_WINDOW_MS: int = 72 * 60 * 60 * 1000

#: Cuánto antes del cierre de ventana debe disparar el watchdog (30 min).
WATCHDOG_PRE_EXPIRY_MS: int = 30 * 60 * 1000


# =============================================================================
# API pública
# =============================================================================


def compute_service_window_expiry(last_inbound_at_ms: int) -> int:
    """Devuelve epoch ms en que cierra la ventana de servicio.

    La ventana se reabre con cada inbound del cliente — el caller debe
    invocar este helper en cada `IngestInboundMessage` y persistir el
    resultado en `metadata.service_window_expires_at_ms`.
    """
    return last_inbound_at_ms + SERVICE_WINDOW_MS


def compute_ctwa_window_expiry(ctwa_first_touch_at_ms: int) -> int:
    """Devuelve epoch ms en que cierra la ventana extendida CTWA.

    La ventana CTWA NO se renueva con inbounds subsecuentes — sólo el
    primer touch que vino con `referral.ctwa_clid` cuenta. El caller debe
    persistir `metadata.ctwa_window_expires_at_ms` sólo la primera vez
    que detecta `ctwa_clid` en un inbound.
    """
    return ctwa_first_touch_at_ms + CTWA_WINDOW_MS


def is_in_service_window(now_ms: int, metadata: dict[str, Any]) -> bool:
    """True si `metadata` tiene una ventana de servicio activa en `now_ms`.

    Falsy/missing `service_window_expires_at_ms` → False. Si está en metadata
    pero ya pasó la expiración → False. Sólo True cuando hay un valor entero
    válido y `now_ms < exp`.
    """
    exp = metadata.get("service_window_expires_at_ms")
    return isinstance(exp, int) and now_ms < exp


def is_in_ctwa_window(now_ms: int, metadata: dict[str, Any]) -> bool:
    """True si la ventana CTWA (72h) sigue abierta en `now_ms`.

    La ventana CTWA otorga `pricing.type=free_entry_point` para cualquier
    mensaje, incluido marketing. Es ortogonal a la ventana de servicio:
    pueden coexistir. Si CTWA está abierta, el cost es 0 incluso si la
    ventana de servicio ya cerró.
    """
    exp = metadata.get("ctwa_window_expires_at_ms")
    return isinstance(exp, int) and now_ms < exp


def is_message_billable(now_ms: int, metadata: dict[str, Any]) -> bool:
    """True si un outbound enviado en `now_ms` esperaría ser cobrado.

    Conveniencia para el caller: si está dentro de cualquier free window
    (servicio O CTWA), Meta NO cobra. Si está fuera de ambas, depende de
    la category (utility/marketing/auth) — el cost real se computa con
    `compute_message_cost_micros` cuando llega el webhook.

    NOTE: este es un PRE-check basado en metadata local. La verdad
    autoritativa viene del `pricing` object del webhook `message_status`.
    """
    return not (
        is_in_service_window(now_ms, metadata) or is_in_ctwa_window(now_ms, metadata)
    )


def watchdog_fire_at(metadata: dict[str, Any]) -> int | None:
    """Devuelve epoch ms en que debe dispararse el watchdog, o None.

    El watchdog se dispara `WATCHDOG_PRE_EXPIRY_MS` antes del cierre de
    la ventana de servicio. Si `metadata.service_window_expires_at_ms`
    no existe o no es int, retorna None (no programar watchdog).
    """
    exp = metadata.get("service_window_expires_at_ms")
    if not isinstance(exp, int):
        return None
    return exp - WATCHDOG_PRE_EXPIRY_MS
