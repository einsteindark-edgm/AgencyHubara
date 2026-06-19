"""Integración: el manifest se compila a un runnable y CORRE sobre el runtime
port (LocalRuntime), con recovery por execution-id, y reusando una tool del
catálogo inyectada por el binding. Todo determinista, sin servidor."""
from __future__ import annotations

import json
from pathlib import Path

from sdk.connectorkit.ports import FixtureMetaInsights
from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest
from sdk.runtime import LocalRuntime

GA = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((GA / "fixtures" / "meta_insights_sample.json").read_text(encoding="utf-8"))
INPUT = {"account_id": "act_1", "since": "2026-06-01", "until": "2026-06-18"}


def _meta_insights_runnable():
    m = load_manifest(GA / "manifests" / "meta-insights.agent.yaml")
    return build_runnable(m, GA, ports={"meta_marketing_api": FixtureMetaInsights(SAMPLE)})


def test_capability_corre_via_local_runtime() -> None:
    rt = LocalRuntime()
    ex = rt.run(_meta_insights_runnable(), INPUT)
    assert ex.status == "completed"
    assert ex.output["blended_roas"] == 2.3333


def test_recupera_por_execution_id() -> None:
    rt = LocalRuntime()
    eid = rt.start_durable(_meta_insights_runnable(), INPUT)  # arranca, NO completa (simula crash)
    assert rt.get(eid).status == "running"
    ex = rt.resume(eid)  # recupera por execution-id
    assert ex.id == eid and ex.status == "completed"
    assert ex.output["blended_roas"] == 2.3333


def test_roas_cac_usa_la_tool_del_catalogo_inyectada_por_el_binding() -> None:
    m = load_manifest(GA / "manifests" / "roas-cac.agent.yaml")
    runnable = build_runnable(m, GA)  # el loader resuelve recommend-budget del catálogo
    out = runnable({"adsets": [{"id": "a", "roas": 2.0}, {"id": "b", "roas": 1.0}], "total_budget": 300.0})
    assert out["allocations"] == [{"id": "a", "budget": 200.0}, {"id": "b", "budget": 100.0}]
    assert out["analyzed_adsets"] == 2
