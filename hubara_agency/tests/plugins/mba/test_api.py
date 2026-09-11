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


def test_agents_and_config_are_served_from_the_authored_files(tenant_env: dict[str, str]) -> None:
    c = _client()
    agents = c.get("/api/mba/agents").json()["agents"]
    assert [a["id"] for a in agents] == ["sales"]
    assert agents[0]["display_name"] == "Asesor de Ventas"
    assert agents[0]["entity_id"] == tenant_env["WHATSAPP_PHONE_NUMBER_ID"]  # el número del tenant, desde el entorno
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


# ── D1.9: agent_event hacia Meta Business Agent (plano de gestión) ───────────


class _AgentEventStub:
    def __init__(self, reason: str, emitted: bool = False) -> None:
        self.reason, self.emitted, self.calls = reason, emitted, []

    async def execute(self, session_key, event_type, *, order_id=None, episode_id=None, message="", payload=None,
                      source=None):
        from src.plugins.mba.use_cases.emit_agent_event import AgentEventOutcome

        self.calls.append((session_key, event_type, order_id, message, payload, source, episode_id))
        return AgentEventOutcome(session_key=session_key, emitted=self.emitted, reason=self.reason,
                                 agent_event_id="AE_1" if self.emitted else None, at_ms=1)


def _agent_event_client(stub: _AgentEventStub) -> TestClient:
    from src.plugins.mba import api as mba_api

    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.dependency_overrides[mba_api.get_emit_agent_event] = lambda: stub
    return TestClient(app)


def test_agent_event_endpoint_delegates_to_the_use_case() -> None:
    stub = _AgentEventStub("accepted", emitted=True)
    c = _agent_event_client(stub)
    r = c.post("/api/mba/sessions/wa_573001234567/agent-events",
               json={"type": "order_shipped", "order_id": "order_1", "message": "Tu pedido va en camino.",
                     "payload": {"stage": "shipping"}, "source": "eta"})
    assert r.status_code == 200
    assert r.json() == {"session_key": "wa_573001234567", "emitted": True, "reason": "accepted",
                        "agent_event_id": "AE_1", "at_ms": 1, "error": None, "recorded": True}
    assert stub.calls == [("wa_573001234567", "order_shipped", "order_1", "Tu pedido va en camino.",
                           {"stage": "shipping"}, "eta", None)]
    r = c.post("/api/mba/sessions/wa_573001234567/agent-events",
               json={"type": "episode_closed", "episode_id": "ep_002", "message": "cerrado"})
    assert r.status_code == 200 and stub.calls[-1][1] == "episode_closed" and stub.calls[-1][6] == "ep_002"


@pytest.mark.parametrize("reason,status", [
    ("mba_disabled", 503), ("customer_not_enabled", 403), ("session_unknown", 404),
    ("hubara_controls", 200), ("already_emitted", 200), ("rejected", 200), ("unavailable", 200),
    ("ambiguous", 200), ("entity_id_missing", 200),
])
def test_agent_event_endpoint_maps_guard_reasons_to_status_codes(reason: str, status: int) -> None:
    r = _agent_event_client(_AgentEventStub(reason)).post(
        "/api/mba/sessions/wa_573001234567/agent-events", json={"type": "order_shipped", "message": "m"})
    assert r.status_code == status
    if status == 200:
        assert r.json()["emitted"] is False and r.json()["reason"] == reason


def test_agent_event_endpoint_validates_key_type_and_message() -> None:
    c = _agent_event_client(_AgentEventStub("accepted", True))
    ok = {"type": "order_shipped", "message": "m"}
    assert c.post("/api/mba/sessions/573001234567/agent-events", json=ok).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={**ok, "type": "whatever"}).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={**ok, "message": ""}).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={**ok, "message": "x" * 2001}).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={"message": "m"}).status_code == 422
    big = {"blob": "x" * (8 * 1024)}
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={**ok, "payload": big}).status_code == 422
    assert c.post("/api/mba/sessions/wa_573001234567/agent-events", json={**ok, "payload": {"a": 1}}).status_code == 200


# ── D2.2: sync de la configuración hacia Meta ───────────────────────────────


class _SyncStub:
    def __init__(self, *, plan=None, outcome=None, plan_error=None) -> None:
        from src.plugins.mba.domain.sync import SyncPlan

        self._plan = plan if plan is not None else SyncPlan("sales", "PHONE_777", (), (), "fp-1")
        self._outcome = outcome
        self._plan_error = plan_error
        self.calls = []

    async def plan(self, agent_id):
        self.calls.append(("plan", agent_id))
        if agent_id != "sales":
            return None
        if self._plan_error is not None:
            raise self._plan_error
        return self._plan

    async def apply(self, agent_id, *, fingerprint=None):
        from src.plugins.mba.use_cases.sync_agent import SyncOutcome

        self.calls.append(("apply", agent_id, fingerprint))
        return self._outcome or SyncOutcome(agent_id, True, "applied", status="ok", plan={"fingerprint": "fp-1"})


def _sync_client(stub: _SyncStub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from src.plugins.mba import api as mba_api

    monkeypatch.setattr(mba_api, "WORKSPACE_VAULT_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.dependency_overrides[mba_api.get_sync_agent] = lambda: stub
    return TestClient(app)


def test_sync_state_is_served_from_the_vault_and_is_empty_before_the_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba.adapters.sync_state import SyncStateStore

    client = _sync_client(_SyncStub(), tmp_path, monkeypatch)
    res = client.get("/api/mba/agents/sales/sync")
    assert res.status_code == 200 and res.json() == {"agent_id": "sales", "state": None}
    SyncStateStore(tmp_path).write("sales", {"ids": {"skills": {"persona": "s-1"}}, "last_apply": {"status": "ok"}})
    assert client.get("/api/mba/agents/sales/sync").json()["state"]["last_apply"] == {"status": "ok"}
    assert client.get("/api/mba/agents/nope/sync").status_code == 404
    assert client.get("/api/mba/agents/..%2Fetc/sync").status_code in (404, 422)


def test_sync_plan_is_read_only_and_reports_a_remote_outage_as_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba.adapters.meta_admin import MbaAdminError

    stub = _SyncStub()
    client = _sync_client(stub, tmp_path, monkeypatch)
    res = client.get("/api/mba/agents/sales/sync/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["fingerprint"] == "fp-1" and body["blocked"] == [] and body["ops"] == [] and body["counts"] == {}
    assert stub.calls == [("plan", "sales")]
    assert client.get("/api/mba/agents/nope/sync/plan").status_code == 404

    down = _SyncStub(plan_error=MbaAdminError("unavailable", status=503, detail="down", attempts=3))
    res = _sync_client(down, tmp_path, monkeypatch).get("/api/mba/agents/sales/sync/plan")
    assert res.status_code == 503 and res.json()["detail"] == {"error": "remote_unavailable", "kind": "unavailable", "status": 503, "detail": "down"}

    unconfigured = _SyncStub(plan_error=MbaAdminError("not_configured", detail="META_MBA_TOKEN no configurado"))
    res = _sync_client(unconfigured, tmp_path, monkeypatch).get("/api/mba/agents/sales/sync/plan")
    assert res.status_code == 503 and res.json()["detail"]["kind"] == "not_configured"


def test_sync_apply_passes_the_confirmed_fingerprint_and_maps_guards_to_status_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba.use_cases.sync_agent import SyncOutcome

    stub = _SyncStub()
    client = _sync_client(stub, tmp_path, monkeypatch)
    res = client.post("/api/mba/agents/sales/sync", json={"fingerprint": "fp-1"})
    assert res.status_code == 200 and res.json()["applied"] is True and res.json()["reason"] == "applied"
    assert stub.calls == [("apply", "sales", "fp-1")]
    # M-5: sin fingerprint no se aplica nada (el único camino de escritura exige revisión previa)
    assert client.post("/api/mba/agents/sales/sync").status_code == 422
    assert client.post("/api/mba/agents/sales/sync", json={}).status_code == 422
    assert client.post("/api/mba/agents/sales/sync", json={"fingerprint": ""}).status_code == 422
    assert stub.calls == [("apply", "sales", "fp-1")]

    for reason, status in (("mba_disabled", 503), ("agent_unknown", 404), ("sync_in_progress", 409), ("remote_unavailable", 503)):
        out = SyncOutcome("sales", False, reason, error={"kind": "unavailable", "detail": "down", "status": 503} if reason == "remote_unavailable" else None)
        res = _sync_client(_SyncStub(outcome=out), tmp_path, monkeypatch).post("/api/mba/agents/sales/sync", json={"fingerprint": "fp-1"})
        assert res.status_code == status, reason
        assert res.json()["detail"]["error"] == reason
    # bloqueado / plan viejo / nada que hacer: 200 con el outcome (la tab lo muestra)
    for reason in ("blocked", "plan_changed", "nothing_to_do"):
        out = SyncOutcome("sales", False, reason, plan={"fingerprint": "fp-2"}, blocked=("placeholder:<FLOW_ID>",) if reason == "blocked" else ())
        res = _sync_client(_SyncStub(outcome=out), tmp_path, monkeypatch).post("/api/mba/agents/sales/sync", json={"fingerprint": "fp-1"})
        assert res.status_code == 200 and res.json()["reason"] == reason and res.json()["applied"] is False
    assert client.post("/api/mba/agents/sales/sync", json={"fingerprint": "x" * 200}).status_code == 422


def test_the_real_sync_agent_is_wired_with_the_vault_store_the_flag_and_the_connector_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba import api as mba_api
    from src.plugins.mba.adapters.meta_admin import MetaMbaAdmin
    from src.plugins.mba.use_cases.sync_agent import SyncAgent
    from src.platform import config

    monkeypatch.setenv("HUBARA_MBA_API_KEY", "k-1")
    monkeypatch.setattr(config, "MBA_STANDBY_ENABLED", False)
    uc = mba_api.get_sync_agent()
    assert isinstance(uc, SyncAgent) and isinstance(uc._admin, MetaMbaAdmin)
    assert uc._api_key() == "k-1" and uc._enabled() is False
    assert uc._load("sales") is not None and uc._load("nope") is None


# ── D2.3: rollout (allowlist, audiencia, enabled) ────────────────────────────


class _RolloutStub:
    def __init__(self, *, status=None, outcome=None, status_error=None) -> None:
        self._status, self._outcome, self._status_error, self.calls = status, outcome, status_error, []

    async def status(self, agent_id):
        from src.plugins.mba.use_cases.rollout_control import RolloutStatus

        self.calls.append(("status", agent_id))
        if agent_id != "sales":
            return None
        if self._status_error is not None:
            raise self._status_error
        return self._status or RolloutStatus(
            agent_id="sales", entity_id="PHONE_777", rollout_enabled=False, ai_audience="ALLOWLISTED_ONLY",
            allowlist=[{"id": "e-1", "phone": "+573001234567", "in_hubara": True}],
            checks=[{"code": "flag_enabled", "ok": True, "detail": ""}], can_enable=True, everyone_allowed=False,
            last_sync=None, history=[],
        )

    def _out(self, *call):
        from src.plugins.mba.use_cases.rollout_control import RolloutOutcome

        self.calls.append(call)
        return self._outcome or RolloutOutcome("sales", True, "applied")

    async def add_phone(self, agent_id, phone):
        return self._out("add_phone", agent_id, phone)

    async def remove_phone(self, agent_id, entry_id):
        return self._out("remove_phone", agent_id, entry_id)

    async def set_audience(self, agent_id, audience, *, confirm):
        return self._out("set_audience", agent_id, audience, confirm)

    async def set_enabled(self, agent_id, enabled, *, confirm):
        return self._out("set_enabled", agent_id, enabled, confirm)


def _rollout_client(stub: _RolloutStub) -> TestClient:
    from src.plugins.mba import api as mba_api

    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.dependency_overrides[mba_api.get_rollout_control] = lambda: stub
    return TestClient(app)


def test_rollout_status_is_served_and_a_remote_outage_is_503() -> None:
    from src.plugins.mba.adapters.meta_admin import MbaAdminError

    stub = _RolloutStub()
    res = _rollout_client(stub).get("/api/mba/agents/sales/rollout")
    assert res.status_code == 200
    body = res.json()
    assert body["rollout_enabled"] is False and body["can_enable"] is True and body["allowlist"][0]["phone"] == "+573001234567"
    assert _rollout_client(stub).get("/api/mba/agents/nope/rollout").status_code == 404
    down = _RolloutStub(status_error=MbaAdminError("unavailable", status=503, detail="down", attempts=3))
    res = _rollout_client(down).get("/api/mba/agents/sales/rollout")
    assert res.status_code == 503 and res.json()["detail"]["error"] == "remote_unavailable"


def test_rollout_writes_delegate_with_the_confirmation_and_validate_input() -> None:
    stub = _RolloutStub()
    c = _rollout_client(stub)
    assert c.post("/api/mba/agents/sales/rollout/allowlist", json={"phone": "+573009876543"}).status_code == 200
    assert c.delete("/api/mba/agents/sales/rollout/allowlist/e-1").status_code == 200
    assert c.put("/api/mba/agents/sales/rollout/audience", json={"ai_audience": "EVERYONE", "confirm": True}).status_code == 200
    assert c.put("/api/mba/agents/sales/rollout/enabled", json={"enabled": True, "confirm": True}).status_code == 200
    assert c.put("/api/mba/agents/sales/rollout/enabled", json={"enabled": False}).status_code == 200
    assert stub.calls == [
        ("add_phone", "sales", "+573009876543"),
        ("remove_phone", "sales", "e-1"),
        ("set_audience", "sales", "EVERYONE", True),
        ("set_enabled", "sales", True, True),
        ("set_enabled", "sales", False, False),
    ]
    # validación de entrada: 422 antes de tocar el use case
    assert c.post("/api/mba/agents/sales/rollout/allowlist", json={"phone": "573009876543"}).status_code == 422
    assert c.post("/api/mba/agents/sales/rollout/allowlist", json={}).status_code == 422
    assert c.put("/api/mba/agents/sales/rollout/audience", json={"ai_audience": "FRIENDS"}).status_code == 422
    assert c.delete("/api/mba/agents/sales/rollout/allowlist/..%2Fx").status_code in (404, 422)  # starlette normaliza el path
    assert c.delete("/api/mba/agents/sales/rollout/allowlist/e%201").status_code == 422
    assert c.put("/api/mba/agents/nope/rollout/enabled", json={"enabled": False}).status_code == 404
    assert len(stub.calls) == 5


@pytest.mark.parametrize(
    "reason,status",
    [("mba_disabled", 503), ("remote_unavailable", 503), ("agent_unknown", 404), ("entity_id_missing", 409), ("unavailable", 503), ("ambiguous", 503), ("not_configured", 503)],
)
def test_rollout_guards_map_to_status_codes_and_policy_refusals_are_200(reason: str, status: int) -> None:
    from src.plugins.mba.use_cases.rollout_control import RolloutOutcome

    out = RolloutOutcome("sales", False, reason, error={"kind": reason, "detail": "x", "status": None})
    res = _rollout_client(_RolloutStub(outcome=out)).put("/api/mba/agents/sales/rollout/enabled", json={"enabled": True, "confirm": True})
    assert res.status_code == status and res.json()["detail"]["error"] == reason
    for policy in ("not_ready", "confirmation_required", "everyone_not_allowed", "customer_not_in_hubara_allowlist", "already_listed", "rejected"):
        out = RolloutOutcome("sales", False, policy, blocked=("sync_ok",) if policy == "not_ready" else ())
        res = _rollout_client(_RolloutStub(outcome=out)).put("/api/mba/agents/sales/rollout/enabled", json={"enabled": True, "confirm": True})
        assert res.status_code == 200 and res.json()["applied"] is False and res.json()["reason"] == policy


def test_the_real_rollout_control_is_wired_with_hubaras_closed_list_and_the_everyone_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba import api as mba_api
    from src.plugins.mba.use_cases.rollout_control import RolloutControl
    from src.platform import config

    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567"}))
    monkeypatch.delenv("MBA_ALLOW_EVERYONE", raising=False)
    uc = mba_api.get_rollout_control()
    assert isinstance(uc, RolloutControl)
    assert uc._hubara_allowed("+573001234567") is True and uc._hubara_allowed("+573000000000") is False
    assert uc._everyone() is False
    monkeypatch.setenv("MBA_ALLOW_EVERYONE", "1")
    assert uc._everyone() is True
    monkeypatch.setenv("MBA_ALLOW_EVERYONE", "PLACEHOLDER_set_out_of_band")
    assert uc._everyone() is False


# ── D2.4: consola agent_test ─────────────────────────────────────────────────


class _AdminTestStub:
    def __init__(self, *, reply=None, error=None) -> None:
        self._reply, self._error, self.calls = reply, error, []

    async def agent_test(self, entity_id, user_msg, *, conversation_id=None):
        self.calls.append((entity_id, user_msg, conversation_id))
        if self._error is not None:
            raise self._error
        return self._reply or {"message_id": "m1", "agent_response": "Hola, soy el asesor.", "conversation_id": "conv-1", "timestamp": 1}


def _agent_test_client(stub: _AdminTestStub, monkeypatch: pytest.MonkeyPatch, entity_id: str | None = "PHONE_777") -> TestClient:
    from src.plugins.mba import api as mba_api
    from src.plugins.mba.service import load_agent as real_load

    def _load(agent_id):
        cfg = real_load(agent_id)
        if cfg is None:
            return None
        from dataclasses import replace

        return replace(cfg, entity_id=entity_id)

    monkeypatch.setattr(mba_api, "load_agent", _load)
    app = FastAPI()
    app.include_router(router, prefix="/api/mba")
    app.dependency_overrides[mba_api.get_mba_admin] = lambda: stub
    return TestClient(app)


def test_agent_test_sends_the_message_to_meta_and_threads_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _AdminTestStub()
    c = _agent_test_client(stub, monkeypatch)
    res = c.post("/api/mba/agents/sales/test", json={"message": "hola"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["reply"]["agent_response"] == "Hola, soy el asesor." and body["reply"]["conversation_id"] == "conv-1"
    res = c.post("/api/mba/agents/sales/test", json={"message": "¿y envíos?", "conversation_id": "conv-1"})
    assert res.status_code == 200
    assert stub.calls == [("PHONE_777", "hola", None), ("PHONE_777", "¿y envíos?", "conv-1")]
    # respuesta con todos los campos opcionales de Meta
    rich = _AdminTestStub(reply={
        "message_id": "m2", "agent_response": "", "conversation_id": "conv-1", "timestamp": 2,
        "no_response_reason": "handoff", "handoff_reason": "customer_asked_for_human", "quick_replies": ["Sí", "No"], "product_variant_ids": ["v1"],
    })
    body = _agent_test_client(rich, monkeypatch).post("/api/mba/agents/sales/test", json={"message": "quiero un humano"}).json()
    assert body["reply"]["handoff_reason"] == "customer_asked_for_human" and body["reply"]["quick_replies"] == ["Sí", "No"]


def test_agent_test_validates_input_and_maps_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.mba.adapters.meta_admin import MbaAdminError

    stub = _AdminTestStub()
    c = _agent_test_client(stub, monkeypatch)
    assert c.post("/api/mba/agents/sales/test", json={}).status_code == 422
    assert c.post("/api/mba/agents/sales/test", json={"message": ""}).status_code == 422
    assert c.post("/api/mba/agents/sales/test", json={"message": "   \n "}).status_code == 422  # solo espacios
    assert c.post("/api/mba/agents/sales/test", json={"message": "x" * 4097}).status_code == 422
    assert c.post("/api/mba/agents/sales/test", json={"message": "hola", "conversation_id": "x" * 200}).status_code == 422
    assert c.post("/api/mba/agents/nope/test", json={"message": "hola"}).status_code == 404
    assert stub.calls == []
    # sin entity_id (número no onboardeado): 409 antes de tocar Meta
    res = _agent_test_client(stub, monkeypatch, entity_id=None).post("/api/mba/agents/sales/test", json={"message": "hola"})
    assert res.status_code == 409 and res.json()["detail"]["error"] == "entity_id_missing" and stub.calls == []
    # Meta caída / token faltante → 503 con el motivo; rechazo de Meta → 200 ok=false con el detalle (la consola lo muestra)
    for kind, status in (("unavailable", 503), ("not_configured", 503), ("ambiguous", 503)):
        down = _AdminTestStub(error=MbaAdminError(kind, status=503 if kind == "unavailable" else None, detail="down"))
        res = _agent_test_client(down, monkeypatch).post("/api/mba/agents/sales/test", json={"message": "hola"})
        assert res.status_code == status and res.json()["detail"]["kind"] == kind
    rejected = _AdminTestStub(error=MbaAdminError("rejected", status=400, detail="agent not onboarded"))
    res = _agent_test_client(rejected, monkeypatch).post("/api/mba/agents/sales/test", json={"message": "hola"})
    assert res.status_code == 200 and res.json() == {"ok": False, "reply": None, "error": {"kind": "rejected", "status": 400, "detail": "agent not onboarded"}}


def test_the_real_admin_dependency_is_the_meta_client() -> None:
    from src.plugins.mba import api as mba_api
    from src.plugins.mba.adapters.meta_admin import MetaMbaAdmin

    assert isinstance(mba_api.get_mba_admin(), MetaMbaAdmin)
