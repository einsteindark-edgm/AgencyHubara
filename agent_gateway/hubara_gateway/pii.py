"""Enmascarado de PII en `/conversations` (mismo formato que `src/shared/views.ts` del plugin)."""

import re
from functools import cache
from typing import Any

from hubara_gateway.schemas import output_schema

_NON_DIGIT = re.compile(r"\D")


def mask_phone(phone: str) -> str:
    digits = _NON_DIGIT.sub("", phone)
    if len(digits) <= 4:
        return "••••"
    return "•" * min(7, len(digits) - 4) + digits[-4:]


def mask_name(name: str) -> str:
    return " ".join(part[0].upper() + "." for part in name.split()) or "—"


@cache
def _contract_fields() -> frozenset[str]:
    """Campos de una conversación según el contrato Zod del dashboard (outputSchema)."""
    item = output_schema("ads_get_conversations")["properties"]["conversations"]["items"]
    return frozenset(item["properties"])


def mask_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    """Fila proyectada sobre el CONTRATO (allowlist), con la PII enmascarada.

    Por allowlist y no por exclusión: un campo nuevo del backend con datos personales
    no llega al modelo hasta que alguien lo agregue al contrato y lo clasifique acá.
    Teléfono, nombre e id (lleva el teléfono) se enmascaran; la ciudad se omite.
    """
    masked = {k: v for k, v in conv.items() if k in _contract_fields()}
    if isinstance(conv.get("phone_number"), str):
        masked["phone_number"] = mask_phone(conv["phone_number"])
    if isinstance(conv.get("name"), str):
        masked["name"] = mask_name(conv["name"])
    if "id" in conv:
        masked["id"] = f"conv-{conv.get('episode_id') or conv.get('started_at_ms')}"
    if "city" in masked:
        masked["city"] = None
    return masked


def mask_conversations_body(body: dict[str, Any]) -> dict[str, Any]:
    convs = body.get("conversations")
    if not isinstance(convs, list):
        return body
    return {**body, "conversations": [mask_conversation(c) if isinstance(c, dict) else c for c in convs]}
