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
    # método equivocado y tool inexistente
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


class _ChatsCast:
    """Doble del cast mba→chats (`deps.chats`): captura y responde fijo."""

    def __init__(self, response=None, exc=None) -> None:
        self.calls = []
        self._response = response or {}
        self._exc = exc

    async def __call__(self, request, session_key, action, body):
        assert request is not None and request.method == "POST"
        self.calls.append((session_key, action, body))
        if self._exc is not None:
            raise self._exc
        return dict(self._response)


def test_write_tools_delegate_to_the_chats_cast_scoped_to_the_customer_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    cast = _ChatsCast({"updated": True, "order_draft": {"producto": "luz-serena"}})
    c = _client_with_deps(monkeypatch, chats=cast)
    ok = {"X-API-Key": "secreto"}
    r = c.post("/api/mba/tools/set_order_slot", headers=ok, json={"customer_phone": "+57 300 123 4567", "producto": "luz-serena", "cantidad": 2})
    assert r.status_code == 200 and r.json()["updated"] is True
    assert cast.calls == [("wa_573001234567", "draft", {"producto": "luz-serena", "cantidad": 2})]
    cast = _ChatsCast({"escalated": True, "already_human": False, "active_route": "humano", "tag": "HUMANO"})
    c = _client_with_deps(monkeypatch, chats=cast)
    r = c.post("/api/mba/tools/escalate_to_human", headers=ok, json={"customer_phone": _PHONE, "reason_category": "BULK_ORDER", "summary": "30"})
    assert r.status_code == 200 and r.json()["escalated"] is True and cast.calls[0][1] == "escalate"
    r = c.post("/api/mba/tools/manage_conversation_tag", headers=ok, json={"customer_phone": _PHONE, "tag": "HUMANO", "motivo": "x"})
    assert r.status_code == 200 and r.json()["error"] == "invalid_tag" and len(cast.calls) == 1
    # el contrato de Meta sigue validando antes del cast: campo requerido ausente → 422, sin llamar a chats
    r = c.post("/api/mba/tools/register_order", headers=ok, json={"customer_phone": _PHONE, "items": [{"handle": "x", "quantity": 1}]})
    assert r.status_code == 422 and len(cast.calls) == 1


def test_a_failing_cast_is_an_explicit_error_for_the_agent_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    c = _client_with_deps(monkeypatch, chats=_ChatsCast(exc=HTTPException(status_code=502, detail="cast mba→chats: provider no disponible")))
    r = c.post("/api/mba/tools/escalate_to_human", headers={"X-API-Key": "secreto"},
               json={"customer_phone": _PHONE, "reason_category": "BULK_ORDER", "summary": "30"})
    assert r.status_code == 200 and r.json()["error"] == "chats_unavailable" and r.json()["applied"] is False


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
    # entero de miles de dígitos: json.loads lanza ValueError (no JSONDecodeError) → 400, jamás 500
    huge = ('{"customer_phone": "%s", "items": [{"handle": "x", "quantity": %s}]}' % (_PHONE, "9" * 5000)).encode()
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers={**ok, "Content-Type": "application/json"}, content=huge)
    assert r.status_code == 400


def test_public_router_caps_body_size_and_rate_limits_per_ip_and_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client_with_deps(monkeypatch)
    ok = {"X-API-Key": "secreto"}
    big = {"customer_phone": _PHONE, "items": [{"handle": "x" * 1500, "quantity": 1}] * 50}
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers=ok, json=big)
    assert r.status_code == 413
    # chunked (sin Content-Length): se corta al superar el tope, sin materializar el body entero
    def _chunks():
        for _ in range(2000):
            yield b"x" * 1024
    r = c.post("/api/mba/tools/verify_order_for_checkout", headers=ok, content=_chunks())
    assert r.status_code == 413
    monkeypatch.setattr(connector, "_rate_limiter", RateLimiter(capacity=2, refill_per_s=0.0))
    codes = [c.get("/api/mba/tools/list_categories", headers=ok, params={"customer_phone": _PHONE}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    # otra tool desde la misma IP tiene su propio bucket; el tráfico sin key válida no lo consume (401, bucket aparte)
    assert c.get("/api/mba/tools/check_order_status", headers=ok, params={"customer_phone": _PHONE}).status_code == 200
    assert c.get("/api/mba/tools/list_categories", headers={"X-API-Key": "otra"}).status_code == 401


def test_rate_limit_keys_on_the_forwarded_client_ip_and_bad_keys_have_their_own_small_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _client_with_deps(monkeypatch)
    ok = {"X-API-Key": "secreto"}
    monkeypatch.setattr(connector, "_rate_limiter", RateLimiter(capacity=1, refill_per_s=0.0))
    # detrás de Caddy el peer es siempre el proxy: la clave es el primer hop de X-Forwarded-For
    a = {**ok, "X-Forwarded-For": "1.1.1.1, 10.0.0.2"}
    b = {**ok, "X-Forwarded-For": "2.2.2.2"}
    assert c.get("/api/mba/tools/list_categories", headers=a, params={"customer_phone": _PHONE}).status_code == 200
    assert c.get("/api/mba/tools/list_categories", headers=a, params={"customer_phone": _PHONE}).status_code == 429
    assert c.get("/api/mba/tools/list_categories", headers=b, params={"customer_phone": _PHONE}).status_code == 200
    # keys inválidas: bucket propio y chico por IP, ANTES de comparar la key (frena el brute force);
    # agotado, esa IP queda en 429 hasta reponer — incluso con la key buena — y no toca el bucket general
    monkeypatch.setattr(connector, "_rate_limiter", RateLimiter(capacity=1, refill_per_s=0.0))
    monkeypatch.setattr(connector, "_bad_key_limiter", RateLimiter(capacity=2, refill_per_s=0.0))
    bad = {"X-API-Key": "nope", "X-Forwarded-For": "3.3.3.3"}
    assert [c.get("/api/mba/tools/list_categories", headers=bad).status_code for _ in range(3)] == [401, 401, 429]
    blocked = {**ok, "X-Forwarded-For": "3.3.3.3"}
    assert c.get("/api/mba/tools/list_categories", headers=blocked, params={"customer_phone": _PHONE}).status_code == 429
    meta = {**ok, "X-Forwarded-For": "4.4.4.4"}  # otra IP con la key buena: su bucket general está intacto
    assert c.get("/api/mba/tools/list_categories", headers=meta, params={"customer_phone": _PHONE}).status_code == 200


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


def test_connector_refuses_customers_outside_the_closed_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lista cerrada del lado de Hubara (ya estamos en producción): una tool
    invocada para un cliente no habilitado no ejecuta NADA y devuelve un error
    explícito para que MBA pase el caso a un colega."""
    from src.platform import config

    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573009876543"}))
    c = _client_with_deps(monkeypatch)
    r = c.get("/api/mba/tools/search_products", params={"customer_phone": "+573001234567", "q": "vela"},
              headers={"X-API-Key": "secreto"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] == "customer_not_enabled" and "colega" in body["message"]
    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567"}))
    r = c.get("/api/mba/tools/search_products", params={"customer_phone": "+573001234567", "q": "vela"},
              headers={"X-API-Key": "secreto"})
    assert r.status_code == 200 and "error" not in r.json()


# ── D1.5: quién controla el hilo (plano de gestión) ──────────────────────────


def test_session_control_is_served_from_the_session_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.plugins.mba import api as mba_api

    monkeypatch.setattr(mba_api, "WORKSPACE_VAULT_DIR", tmp_path)
    store = FilesystemMetadataStore(tmp_path)
    c = _client()
    key = "wa_573001234567"
    assert c.get(f"/api/mba/sessions/{key}/control").status_code == 404
    store.write(key, {"tag": "INTERESADO"})
    assert c.get(f"/api/mba/sessions/{key}/control").json() == {
        "session_key": key, "control_owner": None, "control_owner_since_ms": None,
        "control_owner_updated_at_ms": None, "control_owner_app_id": None, "thread_control": None, "history": [],
    }
    history = [{"owner": "hubara", "at_ms": i} for i in range(30)]
    store.write(key, {"control_owner": "mba", "control_owner_since_ms": 1, "control_owner_updated_at_ms": 2,
                      "control_owner_app_id": "APP_MBA", "control_history": history,
                      "thread_control": {"last_action": "release", "last_ok": True}})
    body = c.get(f"/api/mba/sessions/{key}/control").json()
    assert (body["control_owner"], body["control_owner_since_ms"], body["control_owner_app_id"]) == ("mba", 1, "APP_MBA")
    assert body["thread_control"] == {"last_action": "release", "last_ok": True}  # el operador ve el último release/error
    assert body["history"] == history[-mba_api.CONTROL_HISTORY_LIMIT:]


@pytest.mark.parametrize("bad_key", ["573001234567", "wa_abc", "wa_..", "wa_5730012345671234567", "wa_573001234567%0A"])
def test_session_control_rejects_keys_that_are_not_a_phone_session(bad_key: str) -> None:
    assert _client().get(f"/api/mba/sessions/{bad_key}/control").status_code == 422


# ── D1.6: soltar el hilo (plano de gestión) ──────────────────────────────────


class _ReleaseStub:
    def __init__(self, reason: str, released: bool = False) -> None:
        self.reason, self.released, self.calls = reason, released, []

    async def execute(self, session_key, trigger, *, order_registered=False, agent_event_emitted=False, metadata=None):
        from src.plugins.mba.use_cases.release_thread import ReleaseOutcome

        self.calls.append((session_key, trigger, order_registered, agent_event_emitted, metadata))
        return ReleaseOutcome(session_key=session_key, released=self.released, reason=self.reason,
                              action_at_ms=1 if self.released else None)


def _release_client(stub: _ReleaseStub) -> TestClient:
    from src.plugins.mba import api as mba_api

    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.dependency_overrides[mba_api.get_release_thread] = lambda: stub
    return TestClient(app)


def test_release_endpoint_delegates_to_the_use_case_with_the_trigger_and_facts() -> None:
    from src.plugins.mba.domain.release_policy import ReleaseTrigger

    stub = _ReleaseStub("handoff_resolved", released=True)
    c = _release_client(stub)
    r = c.post("/api/mba/sessions/wa_573001234567/control/release",
               json={"trigger": "handoff_resolved", "metadata": "caso cerrado", "order_registered": True})
    assert r.status_code == 200
    assert r.json() == {"session_key": "wa_573001234567", "released": True, "reason": "handoff_resolved",
                        "action_at_ms": 1, "error": None, "recorded": True}
    assert stub.calls == [("wa_573001234567", ReleaseTrigger.HANDOFF_RESOLVED, True, False, "caso cerrado")]
    # default: manual, sin hechos
    assert c.post("/api/mba/sessions/wa_573001234567/control/release").status_code == 200
    assert stub.calls[-1][1] is ReleaseTrigger.MANUAL


@pytest.mark.parametrize("reason,status", [
    ("mba_disabled", 503), ("customer_not_enabled", 403), ("session_unknown", 404),
    ("already_mba", 200), ("release_pending", 200), ("unavailable", 200), ("rejected", 200),
])
def test_release_endpoint_maps_guard_reasons_to_status_codes(reason: str, status: int) -> None:
    r = _release_client(_ReleaseStub(reason)).post("/api/mba/sessions/wa_573001234567/control/release")
    assert r.status_code == status
    if status == 200:
        assert r.json()["released"] is False and r.json()["reason"] == reason


def test_release_endpoint_validates_key_and_trigger() -> None:
    c = _release_client(_ReleaseStub("manual", True))
    assert c.post("/api/mba/sessions/573001234567/control/release").status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/control/release", json={"trigger": "whatever"}).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/control/release", json={"metadata": "x" * 2001}).status_code == 422
