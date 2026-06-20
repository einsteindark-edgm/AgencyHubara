"""Golden-replay de `ctwa-report`: render determinista + verdict del periodo. Asierta el
verdict exacto + que la tabla, la recomendación y las fechas sin match aparecen."""
from __future__ import annotations

from graphs.ctwa_report import run

BLENDED = {
    "days": [{
        "date": "2026-06-15", "spend_cop": 300000, "inline_link_clicks": 500,
        "conversations_started": 100, "total_orders": 15, "total_revenue_cop": 450000,
        "metrics": {"drop_off_rate": "0.8", "cost_per_conversation_cop": "3000", "mer": "1.5",
                    "global_cpa_cop": "20000", "global_win_rate": "0.15"},
        "diagnosis": {"high_friction": True, "poor_profitability": True, "recommendation": "rotate_creative"},
    }],
    "period": {
        "metrics": {"drop_off_rate": "0.8", "mer": "1.5"},
        "diagnosis": {"recommendation": "rotate_creative"},
    },
    "unmatched": {"meta_only": ["2026-06-16"], "sales_only": ["2026-06-17"]},
}


def test_render_y_verdict() -> None:
    out = run(BLENDED)
    assert out["verdict"] == "rotate_creative"
    md = out["markdown"]
    assert "Hubara — Ads Analytics (CTWA)" in md
    assert "2026-06-15" in md
    assert "rotate_creative" in md
    assert "solo-Meta" in md  # fechas sin match listadas, no ocultas


def test_sin_periodo_es_insufficient() -> None:
    out = run({"days": [], "period": None, "unmatched": {"meta_only": [], "sales_only": []}})
    assert out["verdict"] == "insufficient_data"
    assert "sin días en común" in out["markdown"]


def test_qa_failed_marca_el_reporte_como_no_confiable() -> None:
    # MF-4: si el QA no-self-review no reconcilia, el reporte se marca [ALERTA].
    out = run({**BLENDED, "qa_passed": False})
    assert "[ALERTA]" in out["markdown"]
    assert out["qa_passed"] is False


def test_qa_ok_surface_en_el_reporte() -> None:
    out = run({**BLENDED, "qa_passed": True})
    assert "QA (no-self-review)" in out["markdown"]
    assert out["qa_passed"] is True


CAMPAIGNS = [
    {"campaign_id": "120238728477970317", "campaign_name": "Duo zodiacal",
     "objective": "OUTCOME_ENGAGEMENT", "spend_cop": 896823, "link_clicks": 571,
     "conversations": 70, "conversation_source": "insights", "is_messaging": True},
    {"campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026 - mundia",
     "objective": "OUTCOME_SALES", "spend_cop": 239433, "link_clicks": 446,
     "conversations": 120, "conversation_source": "insights", "is_messaging": True},
    {"campaign_id": "120240351877200317", "campaign_name": "Dia de la madre",
     "objective": "OUTCOME_ENGAGEMENT", "spend_cop": 0, "link_clicks": 0,
     "conversations": 0, "conversation_source": "entities", "is_messaging": True},
]


def test_render_embudo_por_campana_con_fuente_auditable() -> None:
    # El reporte SURFACEA el embudo por-campaña cuando el pod lo computa (campaigns): una
    # fila por campaña con la conversación complementada y su FUENTE visible (insights /
    # entities / none) — el humano ve de dónde salió cada número, no un valor opaco.
    out = run({**BLENDED, "campaigns": CAMPAIGNS})
    md = out["markdown"]
    assert "Embudo por campaña" in md
    # "Día del padre" (entities=0) muestra su conversación recuperada + la fuente:
    assert "Día del padre 2026 - mundia" in md
    assert "120" in md
    assert "insights" in md   # la fuente auditable del complemento, visible en el reporte
    # las 3 campañas aparecen:
    assert "Duo zodiacal" in md and "Dia de la madre" in md


def test_sin_campaigns_no_hay_seccion_de_embudo() -> None:
    # Backward-compatible: sin `campaigns` el reporte es el de siempre (no sección vacía).
    out = run(BLENDED)
    assert "Embudo por campaña" not in out["markdown"]
