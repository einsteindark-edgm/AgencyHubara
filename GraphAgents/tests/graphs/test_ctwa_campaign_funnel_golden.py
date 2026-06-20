"""Golden-replay de `ctwa-campaign-funnel`: compone parse-meta-entities + meta-ads-insights
+ complement-funnel. Prueba que el embudo por-campaña recupera la conversación que el MCP
entities da en 0 (Día del padre, purchase-conversion) desde /insights actions."""
from __future__ import annotations

import json
from pathlib import Path

from graphs.ctwa_campaign_funnel import run
from tools.complement_funnel.impl import run as complement_funnel
from tools.meta_ads_insights.impl import run as meta_ads_insights
from tools.parse_meta_entities.impl import run as parse_meta_entities

GA = Path(__file__).resolve().parents[2]
MCP_FIX = GA / "fixtures" / "mcp_ad_entities.json"

TOOLS = {
    "parse-meta-entities": parse_meta_entities,
    "meta-ads-insights": meta_ads_insights,
    "complement-funnel": complement_funnel,
}

# /insights (Graph, con actions): aporta la conversación de "Día del padre" (purchase-conversion).
INSIGHTS_PAYLOAD = {"account_currency": "COP", "data": [
    {"date_start": "2026-06-15", "campaign_id": "120243118818600317", "campaign_name": "Día del padre",
     "spend": "100000", "inline_link_clicks": "200",
     "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "85"}]},
]}


def test_golden_embudo_por_campania_complementado() -> None:
    entities_payload = json.loads(MCP_FIX.read_text(encoding="utf-8"))
    out = run({"entities_payload": entities_payload, "insights_payload": INSIGHTS_PAYLOAD}, tools=TOOLS)
    by = {c["campaign_id"]: c for c in out["campaigns"]}

    # Día del padre: el entities daba 0; /insights aportó 85 → complementado + reclasificado.
    padre = by["120243118818600317"]
    assert padre["conversations"] == 85
    assert padre["conversation_source"] == "insights"
    assert padre["is_messaging"] is True

    # Duo zodiacal (messaging por entities, sin fila en este /insights) → usa la de entities.
    duo = by["120238728477970317"]
    assert duo["conversations"] == 205
    assert duo["conversation_source"] == "entities"


def test_faltan_payloads_falla_loud() -> None:
    import pytest
    with pytest.raises(ValueError):
        run({"entities_payload": {}}, tools=TOOLS)  # falta insights_payload
