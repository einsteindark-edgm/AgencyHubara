"""Rutas del plugin ``mba``: plano de gestión (protegido) + connector tools (público con API key)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathlib import Path

from src.plugins.mba.api import connector, router
from src.plugins.mba.domain.guard import RateLimiter
from src.plugins.mba.tools import ToolDeps
from src.sdk.runtime import FilesystemMetadataStore


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
    # las tools de escritura responden 501 (contrato registrado; lógica en el siguiente PR de D1.2)
    r = c.post("/api/mba/tools/register_order", headers=ok, json={
        "customer_phone": _PHONE, "items": [{"handle": "x", "quantity": 1}], "ciudad": "Bogotá",
        "direccion": "Cl 1", "telefono": "300", "nombre_recibe": "Ana", "metodo_pago": "contra_entrega",
    })
    assert r.status_code == 501 and r.json()["tool"] == "register_order"
    # método equivocado y tool inexistente NO son 501
    assert c.post("/api/mba/tools/search_products", headers=ok).status_code == 405
    assert c.get("/api/mba/tools/nope", headers=ok).status_code == 404
    assert set(connector.declared_tools()) == {
        "search_products", "list_categories", "get_product_by_handle", "check_order_status",
        "set_order_slot", "verify_order_for_checkout", "register_order",
        "manage_conversation_tag", "escalate_to_human",
    }


_PHONE = "+573001234567"


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        return SearchResult(
            query=q, count=0, truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=0), results=[],
        )

    async def list_categories(self):
        return []

    async def get_by_handle(self, handle: str):
        from src.platform.catalog.errors import ProductNotFoundError

        raise ProductNotFoundError(handle)


def _client_with_deps(monkeypatch: pytest.MonkeyPatch, **overrides) -> TestClient:
    monkeypatch.setenv("HUBARA_MBA_API_KEY", "secreto")
    app = FastAPI()
    app.include_router(connector.router, prefix="/api/mba")
    deps = ToolDeps(catalog=_Catalog(), checkout=None, order_query=None, metadata=FilesystemMetadataStore(Path("/nonexistent")))
    for k, v in overrides.items():
        setattr(deps, k, v)
    app.dependency_overrides[connector.get_tool_deps] = lambda: deps
    return TestClient(app)


def test_read_tools_run_against_the_injected_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client_with_deps(monkeypatch)
    ok = {"X-API-Key": "secreto"}
    r = c.get("/api/mba/tools/search_products", headers=ok, params={"customer_phone": _PHONE, "q": "vela", "limit": "3"})
    assert r.status_code == 200 and r.json()["query"] == "vela" and r.json()["results"] == []
    r = c.get("/api/mba/tools/list_categories", headers=ok, params={"customer_phone": _PHONE})
    assert r.status_code == 200 and r.json() == {"count": 0, "categories": []}
    r = c.get("/api/mba/tools/get_product_by_handle", headers=ok, params={"customer_phone": _PHONE, "handle": "nope"})
    assert r.status_code == 200 and r.json()["found"] is False
    r = c.get("/api/mba/tools/check_order_status", headers=ok, params={"customer_phone": _PHONE})
    assert r.status_code == 200 and r.json()["orders"] == []
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers=ok, json={"customer_phone": _PHONE, "items": [{"handle": "x", "quantity": 1}]})
    assert r.status_code == 200 and r.json()["error"] == "catalog_unavailable"  # sin verificador en este proceso


def test_invalid_calls_are_422_with_the_list_of_problems_and_bad_json_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client_with_deps(monkeypatch)
    ok = {"X-API-Key": "secreto"}
    r = c.get("/api/mba/tools/get_product_by_handle", headers=ok, params={"customer_phone": "abc"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"
    assert any("customer_phone" in e for e in r.json()["errors"]) and any("handle" in e for e in r.json()["errors"])
    r = c.post(
        "/api/mba/tools/verify_order_for_checkout", headers={**ok, "Content-Type": "application/json"}, content=b"{no json"
    )
    assert r.status_code == 400
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers=ok, json=[1, 2])
    assert r.status_code == 400


def test_public_router_caps_body_size_and_rate_limits_per_ip_and_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client_with_deps(monkeypatch)
    ok = {"X-API-Key": "secreto"}
    big = {"customer_phone": _PHONE, "items": [{"handle": "x" * 1500, "quantity": 1}] * 50}
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers=ok, json=big)
    assert r.status_code == 413
    monkeypatch.setattr(connector, "_rate_limiter", RateLimiter(capacity=2, refill_per_s=0.0))
    codes = [c.get("/api/mba/tools/list_categories", headers=ok, params={"customer_phone": _PHONE}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    # otra tool desde la misma IP tiene su propio bucket; y el límite corre ANTES de la auth (protege la key)
    assert c.get("/api/mba/tools/check_order_status", headers=ok, params={"customer_phone": _PHONE}).status_code == 200
    assert c.get("/api/mba/tools/list_categories", headers={"X-API-Key": "otra"}).status_code == 429


def test_connector_key_check_is_constant_time_safe_with_non_ascii_and_does_not_leak_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUBARA_MBA_API_KEY", "secreto")
    c = _client()
    # una key con caracteres no-ASCII NO puede tumbar el endpoint público (500): es 401
    # (los headers HTTP son bytes; Starlette los decodifica latin-1 → str no-ASCII)
    r = c.get("/api/mba/tools/search_products", headers={"X-API-Key": "señuelo-ñ".encode("latin-1")})
    assert r.status_code == 401
    # sin la variable configurada, el 503 no revela el nombre de la variable
    monkeypatch.delenv("HUBARA_MBA_API_KEY")
    r = c.get("/api/mba/tools/search_products", headers={"X-API-Key": "x"})
    assert r.status_code == 503
    assert "HUBARA_MBA_API_KEY" not in r.text
