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

SALES = {"sales": [
    {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
    {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
]}


def test_supervisor_corre_por_su_manifest_y_produce_el_reporte() -> None:
    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    runnable = build_runnable(manifest, GA)  # tools del catálogo auto-inyectadas

    seed = {"meta_insights": json.loads(META_FIX.read_text(encoding="utf-8")), "manual_sales": SALES}
    state = runnable(seed)

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


def test_binding_ausente_falla_loud_no_silencioso() -> None:
    # Si el seed no trae una clave que un binding referencia, el task graph falla LOUD
    # (error de cableado), no corre uno solo en silencio (el bug que motivó G-WIRE/L-10).
    import pytest
    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    runnable = build_runnable(manifest, GA)
    with pytest.raises(RuntimeError):
        runnable({"manual_sales": SALES})  # falta meta_insights → ctwa-insights no puede leerlo
