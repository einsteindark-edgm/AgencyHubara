"""Per-tool TCK de `parse-meta-entities` — golden del parser de `ads_get_ad_entities`
(portado de test_mcp_adapter.py del motor). Valores de un pull real de Hubara.

El caso clave: "Día del padre" (OUTCOME_SALES, purchase-conversion) parsea
`result_type: "Meta purchases"`, `result_count: 0`, `is_messaging: False` — el MCP
entities NO ve su conversación. Esa la complementa `/insights actions` (tool
`complement-funnel`). Ver docs/meta-ctwa-data-acquisition.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.parse_meta_entities.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "parse_meta_entities" / "tool.yaml"
FIX = GA / "fixtures" / "mcp_ad_entities.json"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_golden_parsea_strings_formateados() -> None:
    by = {c["campaign_id"]: c for c in run(payload=json.loads(FIX.read_text(encoding="utf-8")))["campaigns"]}

    # Campaña messaging (CONVERSATIONS): el entities SÍ trae la conversación.
    assert by["120238728477970317"] == {
        "campaign_id": "120238728477970317", "campaign_name": "Duo zodiacal",
        "objective": "OUTCOME_ENGAGEMENT", "spend_cop": 896823, "link_clicks": 571,
        "result_count": 205, "result_type": "Messaging conversations started",
        "cost_per_result_cop": 4375, "is_messaging": True,
    }


def test_purchase_conversion_parsea_pero_is_messaging_false() -> None:
    # "Día del padre" — el MCP entities lo clasifica por su objetivo de COMPRA → 0 + not messaging.
    by = {c["campaign_id"]: c for c in run(payload=json.loads(FIX.read_text(encoding="utf-8")))["campaigns"]}
    padre = by["120243118818600317"]
    assert padre["objective"] == "OUTCOME_SALES"
    assert padre["result_type"] == "Meta purchases"
    assert padre["result_count"] == 0
    assert padre["is_messaging"] is False  # ← por eso hay que complementar con /insights


def test_not_available_y_spend_cero() -> None:
    by = {c["campaign_id"]: c for c in run(payload=json.loads(FIX.read_text(encoding="utf-8")))["campaigns"]}
    madre = by["120240351877200317"]
    assert madre["spend_cop"] == 0
    assert madre["link_clicks"] == 0  # "Not available" → 0


def test_acepta_json_string_doble_encode() -> None:
    # el envelope tiene `ad_entities` como string JSON-encodeado → doble-decode.
    assert len(run(payload=FIX.read_text(encoding="utf-8"))["campaigns"]) == 3
