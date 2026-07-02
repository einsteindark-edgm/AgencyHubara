"""Los endpoints de EDICIÓN del explorer (`/api/validate-connection`, `/api/connect`,
`/api/disconnect`) — mismo patrón que test_viewer_api.py: se testea `api_route` directo
(sin socket), con un ga_root temporal (copia real del catálogo) para mutar sin miedo.

Contrato HTTP: 200 = ok · 422 = la validación/edición rechazó (con `errors` visibles)
· 400 = request malformado. La UI nunca crashea: siempre JSON con `errors`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sdk.manifest_model import load_manifest
from viewer.server import api_route

GA_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def ga(tmp_path: Path) -> Path:
    shutil.copytree(GA_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(GA_ROOT / "tools", tmp_path / "tools")
    return tmp_path


def test_validate_connection_devuelve_contrato(ga):
    status, payload = api_route("POST", "/api/validate-connection", {},
                                {"source": "agent:roas-cac", "target": "tool:diagnose"}, ga_root=ga)
    assert status == 200
    assert payload["ok"] is True
    assert payload["kind"] == "uses"
    assert payload["target_contract"]["inputs"]["payload"]["type"] == "object"


def test_validate_connection_sin_source_es_400(ga):
    status, payload = api_route("POST", "/api/validate-connection", {}, {"target": "tool:diagnose"}, ga_root=ga)
    assert status == 400
    assert "source" in payload["error"]


def test_connect_persiste_y_responde_200(ga):
    status, payload = api_route("POST", "/api/connect", {},
                                {"source": "agent:roas-cac", "target": "tool:diagnose",
                                 "binding": {"payload": "$state.payload"}}, ga_root=ga)
    assert status == 200, payload
    assert payload["ok"] is True
    assert payload["file"] == "manifests/roas-cac.agent.yaml"
    m = load_manifest(ga / "manifests" / "roas-cac.agent.yaml")
    assert any(t.ref_id == "diagnose" for t in m.tools)


def test_connect_invalido_es_422_con_errores_visibles(ga):
    status, payload = api_route("POST", "/api/connect", {},
                                {"source": "agent:roas-cac", "target": "tool:recommend-budget",
                                 "binding": {"adsets": "$state.adsets", "total_budget": "$state.b"}},
                                ga_root=ga)
    assert status == 422
    assert payload["ok"] is False
    assert any("ya" in e for e in payload["errors"])


def test_disconnect_persiste_y_responde_200(ga):
    status, payload = api_route("POST", "/api/disconnect", {},
                                {"source": "agent:ads-analytics", "target": "agent:numbers-qa",
                                 "kind": "agent"}, ga_root=ga)
    assert status == 200, payload
    m = load_manifest(ga / "manifests" / "ads-analytics.taskgraph.yaml")
    assert all(a.ref_agent_id != "numbers-qa" for a in m.agents)


def test_disconnect_port_es_422(ga):
    status, payload = api_route("POST", "/api/disconnect", {},
                                {"source": "agent:ctwa-report", "target": "port:llm",
                                 "kind": "consumes"}, ga_root=ga)
    assert status == 422
    assert any("port" in e for e in payload["errors"])


def test_disconnect_inexistente_es_422(ga):
    status, payload = api_route("POST", "/api/disconnect", {},
                                {"source": "agent:roas-cac", "target": "tool:diagnose",
                                 "kind": "uses"}, ga_root=ga)
    assert status == 422
    assert any("desconectar" in e or "conectado" in e for e in payload["errors"])
