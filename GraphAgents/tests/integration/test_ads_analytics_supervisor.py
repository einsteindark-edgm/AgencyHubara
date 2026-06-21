"""El DoD del FEATURE (no de las unidades): el task graph corre POR SU MANIFEST.

`build_runnable(ads-analytics.taskgraph.yaml)` threadea el ESTADO por los 5 agentes — el
DAG real (2 extractores → analyzer → qa → reporter) cableado por los `inputs:` del manifest,
con las tools del catálogo auto-inyectadas. Esto es lo que L-10 marcó como faltante: correr
el grafo por su manifest, NO encadenar los `run()` a mano. Ahora es verde.
"""
from __future__ import annotations

import json
from pathlib import Path

from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest

GA = Path(__file__).resolve().parents[2]
META_FIX = GA / "fixtures" / "meta_insights_campaigns.json"
ENT_FIX = GA / "fixtures" / "mcp_ad_entities.json"

SALES = {"sales": [
    {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
    {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
]}


def _seed() -> dict:
    """El seed del pod = lo que el central deposita: Graph /insights, las ventas manuales
    y el envelope crudo de `ads_get_ad_entities` (para el embudo por-campaña)."""
    return {
        "meta_insights": json.loads(META_FIX.read_text(encoding="utf-8")),
        "manual_sales": SALES,
        "entities_payload": json.loads(ENT_FIX.read_text(encoding="utf-8")),
    }


def test_supervisor_corre_por_su_manifest_y_produce_el_reporte() -> None:
    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    runnable = build_runnable(manifest, GA)  # tools del catálogo auto-inyectadas

    state = runnable(_seed())

    # el task graph threadeó las 5 etapas y dejó el reporte final en el estado:
    assert "markdown" in state and "verdict" in state
    assert "Hubara — Ads Analytics (CTWA)" in state["markdown"]
    # los 2 extractores corrieron y alimentaron al analyzer (DAG fan-in):
    assert state["currency"] == "COP"
    assert [d["date"] for d in state["days"]] == ["2026-06-15", "2026-06-16"]
    # la conversación de "Día del padre" (purchase-conversion) entró vía Graph /insights:
    assert state["days"][0]["conversations_started"] == 160
    # el QA no-self-review corrió y reconcilió (días + periodo):
    assert state["passed"] is True
    assert state["verdict"] in (
        "scale_budget", "rotate_creative", "review_targeting_or_pricing",
        "no_revenue_recorded", "insufficient_data",
    )


def test_el_pod_entrega_blend_y_embudo_por_campana() -> None:
    # El pod corre POR SU MANIFEST y entrega DOS cosas en un solo run: el blend de cuenta
    # (markdown/verdict) Y el embudo por-campaña complementado (state["campaigns"]). El
    # complemento NO se arma a mano — lo produce el task graph al componer ctwa-campaign-funnel.
    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    state = build_runnable(manifest, GA)(_seed())

    # 1) el blend de cuenta sigue ahí (no se rompió al sumar el embudo):
    assert "markdown" in state and state["currency"] == "COP"

    # 2) el embudo por-campaña corrió y dejó las 3 campañas del MCP entities:
    campaigns = {c["campaign_id"]: c for c in state["campaigns"]}
    assert set(campaigns) == {"120238728477970317", "120243118818600317", "120240351877200317"}

    # 3) EL COMPLEMENTO en el pod: "Día del padre" (purchase-conversion → entities da
    #    "0 (Meta purchases)") recupera su conversación de Graph /insights actions y se
    #    reclasifica como messaging — todo dentro del run del manifest, no a mano.
    padre = campaigns["120243118818600317"]
    assert padre["conversations"] == 120
    assert padre["conversation_source"] == "insights"
    assert padre["is_messaging"] is True

    # 4) y el reporte SURFACEA el embudo (el humano lo ve, no queda solo en el estado):
    assert "Embudo por campaña" in state["markdown"]
    assert "Día del padre 2026 - mundia" in state["markdown"]


def test_binding_ausente_falla_loud_no_silencioso() -> None:
    # Si el seed no trae una clave que un binding referencia, el task graph falla LOUD
    # (error de cableado), no corre uno solo en silencio (el bug que motivó G-WIRE/L-10).
    import pytest
    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    runnable = build_runnable(manifest, GA)
    with pytest.raises(RuntimeError):
        runnable({"manual_sales": SALES})  # falta meta_insights → ctwa-insights no puede leerlo
