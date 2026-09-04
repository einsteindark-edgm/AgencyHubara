"""Rutas del plugin ``mba``: plano de gestión (protegido) + connector tools (público con API key)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.mba.api import connector, router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.include_router(connector.router, prefix="/api/mba")
    return TestClient(app)


def test_agents_and_config_are_served_from_the_authored_files() -> None:
    c = _client()
    agents = c.get("/api/mba/agents").json()["agents"]
    assert [a["id"] for a in agents] == ["sales"]
    assert agents[0]["display_name"] == "Asesor de Ventas"
    cfg = c.get("/api/mba/agents/sales/config").json()
    assert cfg["agent_id"] == "sales"
    assert cfg["workspace"] == "hubara_agency/src/plugins/mba/agents/sales"
    assert cfg["requests"][0]["section"] == "business_info"
    assert cfg["problems"] == []
    assert c.get("/api/mba/agents/nope/config").status_code == 404


def test_connector_router_is_public_but_fails_closed_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert connector.PUBLIC_ROUTER is True
    monkeypatch.delenv("HUBARA_MBA_API_KEY", raising=False)
    c = _client()
    assert c.get("/api/mba/tools/search_products", headers={"X-API-Key": "x"}).status_code == 503


def test_connector_tools_exist_for_every_declared_tool_and_require_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUBARA_MBA_API_KEY", "secreto")
    c = _client()
    assert c.get("/api/mba/tools/search_products").status_code == 401
    assert c.get("/api/mba/tools/search_products", headers={"X-API-Key": "otro"}).status_code == 401
    ok = {"X-API-Key": "secreto"}
    # cada tool declarada en agent.yaml responde en su método (501 hasta D1.2: el contrato existe, la lógica no)
    r = c.get("/api/mba/tools/search_products", headers=ok)
    assert r.status_code == 501 and r.json()["tool"] == "search_products"
    r = c.post("/api/mba/tools/register_order", headers=ok, json={"customer_phone": "+57..."})
    assert r.status_code == 501 and r.json()["tool"] == "register_order"
    # método equivocado y tool inexistente NO son 501
    assert c.post("/api/mba/tools/search_products", headers=ok).status_code == 405
    assert c.get("/api/mba/tools/nope", headers=ok).status_code == 404
    assert set(connector.declared_tools()) == {
        "search_products", "list_categories", "get_product_by_handle", "check_order_status",
        "set_order_slot", "verify_order_for_checkout", "register_order",
        "manage_conversation_tag", "escalate_to_human",
    }
