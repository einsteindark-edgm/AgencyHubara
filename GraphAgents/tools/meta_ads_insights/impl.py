"""Lógica PURA de `meta-ads-insights` — G-AGNOSTIC: NO importa ningún runtime; solo
stdlib. Parsea la respuesta de Graph /insights (con `actions`) a filas CTWA
normalizadas.

RECIBE el JSON (el proyecto central lo fetchea con OAuth + ads_read read-only y lo
deposita en el seam); esta tool NO llama a Meta → cero superficie de ban. La
conversación de mensajería sale del `actions` como
`onsite_conversion.messaging_conversation_started_7d` — la señal que el MCP
`ads_get_ad_entities` NO expone en campañas purchase-conversion (ver docs/dev-error-log.md).

Portada de ads-analytics-engine/src/ads_engine/meta_insights.py.
"""
from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

# La acción CTWA "conversación de mensajería iniciada (7d)" en Graph /insights.
CTWA_ACTION_TYPE = "onsite_conversion.messaging_conversation_started_7d"


def _spend_to_int(spend: object) -> int:
    # Meta manda spend como string en unidades mayores de la cuenta (COP, sin decimales).
    return int(Decimal(str(spend)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _conversations_from_actions(actions: object) -> int:
    # Graph a veces serializa los nested fields como JSON-string → des-serializar
    # primero. NUNCA iterar un string crudo (iteraría caracteres → conv=0 MUDO, el
    # bug más peligroso: toda la tesis del feature es recuperar la conv del `actions`).
    if isinstance(actions, (str, bytes)):
        actions = json.loads(actions)
    if actions is None:
        return 0
    if not isinstance(actions, list):
        raise ValueError(
            f"'actions' con forma inesperada ({type(actions).__name__}); se esperaba "
            "una lista de {action_type, value} (o su JSON-string)"
        )
    for action in actions:
        if isinstance(action, dict) and action.get("action_type") == CTWA_ACTION_TYPE:
            return int(Decimal(str(action.get("value", 0))))
    return 0


def _row(row: dict) -> dict:
    return {
        "date": str(row["date_start"]),
        "spend_cop": _spend_to_int(row.get("spend", 0)),
        "inline_link_clicks": int(Decimal(str(row.get("inline_link_clicks", 0)))),
        "messaging_conversations_started": _conversations_from_actions(row.get("actions")),
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
    }


def run(*, payload: object) -> dict:
    """payload = dict de Graph /insights (o su JSON string) → filas normalizadas.

    Acepta el envelope ``{"account_currency", "data": [...]}`` o una lista de filas.
    """
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        rows = payload.get("data", [])
        currency = payload.get("account_currency", "COP")
    else:
        rows = payload
        currency = "COP"
    return {"currency": currency, "insights": [_row(r) for r in rows]}
