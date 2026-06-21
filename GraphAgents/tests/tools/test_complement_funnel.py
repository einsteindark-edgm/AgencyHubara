"""Per-tool TCK de `complement-funnel` — el COMPLEMENTO: la conversación que el MCP
entities NO da (purchase-conversion → 0) se recupera de Graph /insights `actions`, con
la fuente auditada. Es la pieza que materializa el playbook de adquisición CTWA
(docs/meta-ctwa-data-acquisition.md)."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.complement_funnel.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "complement_funnel" / "tool.yaml"

# "Día del padre" tal cual lo da el MCP entities: purchase-conversion → 0, no messaging.
PADRE_ENTITY = {
    "campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026",
    "objective": "OUTCOME_SALES", "spend_cop": 239433, "link_clicks": 446,
    "result_count": 0, "result_type": "Meta purchases", "is_messaging": False,
}


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_complemento_recupera_la_conversacion_de_insights() -> None:
    # el entities da 0; /insights actions trae 70+50=120 → se recupera, fuente auditada.
    out = run(payload={
        "entities": [PADRE_ENTITY],
        "insights": [
            {"campaign_id": "120243118818600317", "date": "2026-06-15", "messaging_conversations_started": 70},
            {"campaign_id": "120243118818600317", "date": "2026-06-16", "messaging_conversations_started": 50},
        ],
    })["campaigns"]
    assert out == [{
        "campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026",
        "objective": "OUTCOME_SALES", "spend_cop": 239433, "link_clicks": 446,
        "conversations": 120, "conversation_source": "insights", "is_messaging": True,
    }]


def test_sin_insights_usa_la_de_entities_si_es_messaging() -> None:
    entity = {"campaign_id": "c1", "campaign_name": "x", "objective": "OUTCOME_ENGAGEMENT",
              "spend_cop": 100, "link_clicks": 10, "result_count": 8,
              "result_type": "Messaging conversations started", "is_messaging": True}
    out = run(payload={"entities": [entity], "insights": []})["campaigns"][0]
    assert out["conversations"] == 8
    assert out["conversation_source"] == "entities"


def test_sin_senal_en_ninguna_fuente_es_cero() -> None:
    entity = {"campaign_id": "c2", "campaign_name": "y", "objective": "LINK_CLICKS",
              "spend_cop": 50, "link_clicks": 5, "result_count": 0,
              "result_type": "Link clicks", "is_messaging": False}
    out = run(payload={"entities": [entity], "insights": []})["campaigns"][0]
    assert out["conversations"] == 0
    assert out["conversation_source"] == "none"
