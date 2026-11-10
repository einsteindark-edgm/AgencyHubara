"""Enmascarado de PII en `/conversations` (mismo formato que `src/shared/views.ts` del plugin).

Cada campo de una conversación está CLASIFICADO a mano: seguro (viaja tal cual) o personal
(se tapa). Las listas no se derivan del schema generado a propósito: regenerar los schemas
cuando el dashboard agrega un campo no puede desclasificar un dato personal. Un campo del
contrato sin clasificar hace fallar `tests/test_pii.py` y, mientras tanto, no viaja.
"""

import re
from typing import Any

_NON_DIGIT = re.compile(r"\D")
# No identifican ni ubican a una persona: métricas, costos, estado y texto del anuncio.
SAFE_FIELDS: frozenset[str] = frozenset(
    {
        "ad_headline",
        "agent",
        "audio_calls",
        "audio_cost_usd_micros",
        "capi_event",
        "duration_ms",
        "episode_id",
        "jev_calls",
        "jev_cost_usd_micros",
        "last_msg_at_ms",
        "llm_cost_usd",
        "llm_tokens",
        "msgs_count",
        "started_at_ms",
        "state",
        "state_reason",
        "value",
        "vision_calls",
        "vision_cost_usd_micros",
        "wa_cost_by_category",
        "wa_cost_usd_micros",
        "wa_msgs_pending",
    }
)
# Identifican o ubican al cliente. `mask_conversation` decide cómo sale cada uno.
PERSONAL_FIELDS: frozenset[str] = frozenset({"phone_number", "name", "id", "city", "neighborhood"})
# Personales que se omiten del todo (el contrato los admite en null).
_HIDDEN_FIELDS = ("city", "neighborhood")
# Nivel superior del body: nada más viaja enmascarado (`conversations` se tapa fila a fila).
_BODY_FIELDS = frozenset({"campaign_id", "conversations", "orders_stale"})


def mask_phone(phone: str) -> str:
    digits = _NON_DIGIT.sub("", phone)
    if len(digits) <= 4:
        return "••••"
    return "•" * min(7, len(digits) - 4) + digits[-4:]


def mask_name(name: str) -> str:
    return " ".join(part[0].upper() + "." for part in name.split()) or "—"


def mask_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    """Fila con solo los campos clasificados: los seguros tal cual, los personales tapados.

    Teléfono y nombre se enmascaran, el id (lleva el teléfono) se reemplaza, y la ciudad
    y el barrio de entrega se omiten. Lo que no está clasificado no viaja.
    """
    masked = {k: v for k, v in conv.items() if k in SAFE_FIELDS}
    if "phone_number" in conv:
        phone = conv["phone_number"]
        masked["phone_number"] = mask_phone(phone) if isinstance(phone, str) else None
    if "name" in conv:
        name = conv["name"]
        masked["name"] = mask_name(name) if isinstance(name, str) else None
    if "id" in conv:
        masked["id"] = f"conv-{conv.get('episode_id') or conv.get('started_at_ms')}"
    for hidden in _HIDDEN_FIELDS:
        if hidden in conv:
            masked[hidden] = None
    return masked


def mask_conversations_body(body: dict[str, Any]) -> dict[str, Any]:
    """Body enmascarado: solo los campos de nivel superior conocidos y solo filas-objeto."""
    masked = {k: v for k, v in body.items() if k in _BODY_FIELDS}
    convs = body.get("conversations")
    if isinstance(convs, list):
        masked["conversations"] = [mask_conversation(c) for c in convs if isinstance(c, dict)]
    return masked
