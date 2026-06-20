"""Per-tool TCK de `meta-ads-insights` — parsea Graph /insights (con `actions`) a
filas CTWA normalizadas. Golden inyectando el JSON (la forma que el central deposita).

El caso "Día del padre 2026" demuestra el valor: vía Graph /insights el `actions` trae
la conversación de mensajería AUNQUE la campaña sea purchase-conversion (donde el MCP
`ads_get_ad_entities` reportaba "Meta purchases"=0). Ver docs/dev-error-log.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.meta_ads_insights.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "meta_ads_insights" / "tool.yaml"
FIX = GA / "fixtures" / "meta_insights_campaigns.json"

EXPECTED = [
    {"date": "2026-06-15", "spend_cop": 120000, "inline_link_clicks": 80,
     "messaging_conversations_started": 40, "campaign_id": "120238728477970317",
     "campaign_name": "Duo zodiacal"},
    {"date": "2026-06-16", "spend_cop": 100000, "inline_link_clicks": 50,
     "messaging_conversations_started": 30, "campaign_id": "120238728477970317",
     "campaign_name": "Duo zodiacal"},
    {"date": "2026-06-15", "spend_cop": 176294, "inline_link_clicks": 344,
     "messaging_conversations_started": 120, "campaign_id": "120243118818600317",
     "campaign_name": "Día del padre 2026"},
    {"date": "2026-06-16", "spend_cop": 90000, "inline_link_clicks": 60,
     "messaging_conversations_started": 0, "campaign_id": "120243118818600317",
     "campaign_name": "Día del padre 2026"},
]


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_golden_parsea_graph_insights() -> None:
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    out = run(payload=payload)
    assert out["currency"] == "COP"
    assert out["insights"] == EXPECTED


def test_purchase_conversion_recupera_la_conversacion() -> None:
    # "Día del padre" es purchase-conversion (el MCP daba "Meta purchases"=0). Vía Graph
    # /insights el `actions` SÍ trae la conversación → 120 el día 15. Esa es la señal
    # que el MCP ads_get_ad_entities NO podía dar.
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    row = next(
        r for r in run(payload=payload)["insights"]
        if r["campaign_id"] == "120243118818600317" and r["date"] == "2026-06-15"
    )
    assert row["messaging_conversations_started"] == 120


def test_sin_action_de_mensajeria_da_cero() -> None:
    # Día sin actions → 0 conversaciones (None/insufficient lo decide el analyzer, no acá).
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    row = next(
        r for r in run(payload=payload)["insights"]
        if r["campaign_id"] == "120243118818600317" and r["date"] == "2026-06-16"
    )
    assert row["messaging_conversations_started"] == 0


def test_acepta_json_string() -> None:
    assert run(payload=FIX.read_text(encoding="utf-8"))["insights"] == EXPECTED


def test_idempotente() -> None:
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    assert run(payload=payload) == run(payload=payload)


def test_actions_como_json_string_se_des_serializa_no_se_pierde() -> None:
    # MF-9: Graph puede serializar `actions` como string. NO debe dar 0 en silencio.
    row = {"date_start": "2026-06-15", "spend": "1000", "inline_link_clicks": "10",
           "actions": json.dumps([{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "7"}])}
    out = run(payload={"data": [row]})
    assert out["insights"][0]["messaging_conversations_started"] == 7  # no 0


def test_actions_forma_inesperada_explota_no_silencia() -> None:
    # MF-9: una forma imposible de `actions` (ej. un int) debe fallar LOUD, no dar 0.
    import pytest
    row = {"date_start": "2026-06-15", "spend": "1000", "inline_link_clicks": "10", "actions": 42}
    with pytest.raises(ValueError):
        run(payload={"data": [row]})
