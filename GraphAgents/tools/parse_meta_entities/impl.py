"""Lógica PURA de `parse-meta-entities` — G-AGNOSTIC: solo stdlib. Parsea la respuesta
de `ads_get_ad_entities` (MCP oficial) a filas por-campaña. El MCP devuelve STRINGS
FORMATEADOS ("$ 896.823 COP", "205 (Messaging conversations started)") y `results`
depende del objetivo → esto los parsea determinísticamente. RECIBE el JSON (no llama a Meta).

OJO (ver `docs/meta-ctwa-data-acquisition.md`): en campañas purchase-conversion el
`results` viene "Meta purchases"=0 (brecha de atribución) → la conversación NO está acá;
hay que COMPLEMENTAR con Graph /insights `actions` (tool `complement-funnel`).

Portada de ads-analytics-engine/src/ads_engine/meta_mcp.py.
"""
from __future__ import annotations

import json
import re

_MESSAGING_RESULT_LABEL = "messaging conversations started"
_NOT_AVAILABLE = "not available"
_DIGITS = re.compile(r"\d+")


def _parse_cop(text: object) -> int:
    """'$ 896.823 COP' / '$ 4.375 COP (label)' → 896823. NA/blank → 0. (COP no tiene
    decimales: todo punto es separador de miles → sacá todo lo no-dígito del monto.)"""
    head = str(text or "").split("(")[0]
    if _NOT_AVAILABLE in head.lower():
        return 0
    digits = re.sub(r"[^\d]", "", head)
    return int(digits) if digits else 0


def _parse_count(text: object) -> int:
    """'571' → 571 ; 'Not available' / '' → 0."""
    s = str(text or "")
    if _NOT_AVAILABLE in s.lower():
        return 0
    m = _DIGITS.search(s)
    return int(m.group()) if m else 0


def _parse_result(value: object) -> tuple[int, str]:
    """'205 (Messaging conversations started)' → (205, 'Messaging conversations started')."""
    s = str(value or "")
    count_match = _DIGITS.search(s)
    count = int(count_match.group()) if count_match else 0
    label_match = re.search(r"\(([^)]*)\)", s)
    label = label_match.group(1).strip() if label_match else ""
    return count, label


def _entity_to_dict(entity: dict) -> dict:
    result_count, result_type = _parse_result((entity.get("results") or {}).get("value"))
    cpr_raw = (entity.get("cost_per_result") or {}).get("value")
    cost_per_result = None
    if cpr_raw is not None and _NOT_AVAILABLE not in str(cpr_raw).lower():
        cost_per_result = _parse_cop(cpr_raw)
    return {
        "campaign_id": str(entity.get("id", "")),
        "campaign_name": str(entity.get("name", "")),
        "objective": str(entity.get("objective", "")),
        "spend_cop": _parse_cop(entity.get("amount_spent")),
        "link_clicks": _parse_count(entity.get("actions:link_click")),
        "result_count": result_count,
        "result_type": result_type,
        "cost_per_result_cop": cost_per_result,
        # is_messaging SOLO si el result_type ES messaging (purchase-conversion → False):
        "is_messaging": _MESSAGING_RESULT_LABEL in (result_type or "").lower(),
    }


def run(*, payload: object) -> dict:
    """payload = el dict de `ads_get_ad_entities` (`{ad_entities: '<json>', ...}`) o su JSON
    string. `ad_entities` viene como string JSON-encodeado → doble-decode."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    entities = payload.get("ad_entities", payload) if isinstance(payload, dict) else payload
    if isinstance(entities, (str, bytes)):
        entities = json.loads(entities)
    return {"campaigns": [_entity_to_dict(e) for e in entities]}
