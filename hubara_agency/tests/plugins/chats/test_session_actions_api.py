"""Contrato HTTP `session-actions@v1` de chats (D1.2b).

Lo consume el plugin `mba` por cast (canal 3) para las 4 tools de escritura
que Meta Business Agent invoca: `set_order_slot` → /draft, `register_order` →
/order, `manage_conversation_tag` → /tag, `escalate_to_human` → /escalate.
Cada endpoint resuelve el episodio ACTIVO de la sesión y deja en el vault
exactamente lo que dejarían las tools del agente Sales (gotcha 1: se verifica
el metadata.json real, no el schema).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.platform.catalog.errors import ProductNotFoundError
from src.platform.constants import ROUTE_HUMANO
from src.platform.orders.port import OrderRegistrationResult
from src.plugins.chats.api import session_actions
from src.plugins.chats.api.session_actions import SessionActionsDeps

_A = "wa_573001234567"
_B = "wa_573009876543"

_LUZ = CatalogProductDTO(
    id="p1", handle="luz-serena", title="Luz Serena", status="published",
    variants=[CatalogVariantDTO(id="v1", title="Lavanda / Blanco", options={"Aroma": "Lavanda", "Color": "Blanco"},
                                prices=[CatalogPriceDTO(amount="29000", currency_code="cop")])],
    options={"Aroma": ["Lavanda"], "Color": ["Blanco"]},
    tags=["aroma:Lavanda", "color:Blanco"],
)
_ZODIAC = CatalogProductDTO(
    id="p2", handle="duo-zodiacal", title="Dúo Zodiacal", status="published", description="Set con portavelas.",
    variants=[CatalogVariantDTO(id="z1", title="Leo", options={"Signo": "Leo"},
                                prices=[CatalogPriceDTO(amount="52000", currency_code="cop")])],
    options={"Signo": ["Leo"]},
)


class _Catalog:
    def __init__(self, products=(_LUZ, _ZODIAC)) -> None:
        self._by = {p.handle: p for p in products}

    async def get_by_handle(self, handle: str):
        if handle not in self._by:
            raise ProductNotFoundError(handle)
        return self._by[handle]

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        results = list(self._by.values())[:limit]
        return SearchResult(query=q, count=len(results), truncated=False, stale=False,
                            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=len(results)),
                            results=results)


@dataclass
class _Port:
    ok: bool = True
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kw: Any) -> OrderRegistrationResult:
        self.calls.append(kw)
        if not self.ok:
            return OrderRegistrationResult(success=False, order_id=None, provider="medusa", error_detail="down")
        return OrderRegistrationResult(
            success=True, order_id=f"order_{len(self.calls)}", provider="medusa", customer_id="cus_1",
            raw_payload={"display_id": 22, "items": [{"title": "Luz Serena"}]},
        )


@dataclass
class _Harness:
    client: TestClient
    vault: Path
    port: _Port
    flushed: list[str]
    closed: list[tuple[str, str, str]]

    def meta(self, session: str = _A) -> dict[str, Any]:
        return json.loads((self.vault / session / "metadata.json").read_text(encoding="utf-8"))


@pytest.fixture
def h(tmp_path: Path) -> _Harness:
    port = _Port()
    flushed: list[str] = []
    closed: list[tuple[str, str, str]] = []

    async def flush(session_key: str) -> int:
        flushed.append(session_key)
        return 1

    async def notify(session_key: str, episode_id: str, closing_tag: str) -> None:
        closed.append((session_key, episode_id, closing_tag))

    deps = SessionActionsDeps(vault_dir=tmp_path, catalog=_Catalog(), order_port=port, flush=flush, notify_episode_closed=notify)
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    return _Harness(TestClient(app), tmp_path, port, flushed, closed)


def _url(action: str, session: str = _A) -> str:
    return f"/api/chats/session-actions/{session}/{action}"


_ORDER = {
    "items": [{"handle": "luz-serena", "variant_label": "Lavanda, Blanco", "quantity": 2}],
    "shipping": {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Cl 1 # 2-3", "phone": "3001234567",
                 "receiver_name": "Ana Pérez"},
    "payment_method": "transfer",
}


# ── /draft ────────────────────────────────────────────────────────────────────


def test_draft_writes_the_slots_into_the_active_episode(h: _Harness) -> None:
    r = h.client.post(_url("draft"), json={"producto": "luz-serena", "aroma": "Lavanda", "cantidad": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True and body["order_draft"]["producto"] == "luz-serena"
    ep = h.meta()["episodes"][-1]
    assert ep.get("closed_at_ms") is None
    assert ep["order_draft"]["slots"] == {"producto": "luz-serena", "aroma": "Lavanda", "cantidad": "2"}
    # sobrescribe + valida contra el catálogo (color inexistente → rechazado con los válidos)
    r = h.client.post(_url("draft"), json={"color": "Verde"})
    assert r.status_code == 200
    assert [x["field"] for x in r.json()["rejected"]] == ["color"] and r.json()["rejected"][0]["available"] == ["Blanco"]
    assert "color" not in h.meta()["episodes"][-1]["order_draft"]["slots"]


def test_draft_rejects_unknown_fields_and_unsafe_session_keys(h: _Harness) -> None:
    assert h.client.post(_url("draft"), json={"hack": "x"}).status_code == 422
    assert h.client.post(_url("draft", "wa_.."), json={"aroma": "x"}).status_code == 422
    assert h.client.post(_url("draft", "wa_573001234567%0A"), json={"aroma": "x"}).status_code == 422
    assert h.client.post(_url("draft", "wa_573001234567x"), json={"aroma": "x"}).status_code == 422
    assert h.client.post(_url("draft", "wa_abc"), json={"aroma": "x"}).status_code == 422
    assert not [p for p in h.vault.iterdir() if p.name.startswith("wa_")]


# ── /order ────────────────────────────────────────────────────────────────────


def test_order_prices_server_side_registers_closes_escalates_and_sends_payment_instructions(h: _Harness) -> None:
    r = h.client.post(_url("order"), json=_ORDER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"] is True and body["order_id"] == "order_1" and body["already_registered"] is False
    assert body["order_reference"] == "#22 (Luz Serena)"
    assert (body["subtotal_cop"], body["shipping_cop"], body["total_cop"]) == (58000, 7900, 65900)
    assert body["items"][0] == {"handle": "luz-serena", "variant_label": "Lavanda, Blanco", "quantity": 2,
                                "unit_price_cop": 29000, "title": "Luz Serena", "variant_resolved": True}
    assert body["portavelas_included"] is False and body["portavelas_handles"] == []
    assert body["payment_instructions_sent"] is True and h.flushed == [_A]
    # el port recibió los precios recomputados (SEC-07) — nunca los de Meta (no manda)
    call = h.port.calls[0]
    assert call["session_key"] == _A and call["subtotal_cop"] == 58000 and call["total_cop"] == 65900
    assert call["items"][0].unit_price_cop == 29000 and call["items"][0].variant_label == "Lavanda, Blanco"
    assert call["shipping"].receiver_name == "Ana Pérez" and call["shipping"].neighborhood == "Chapinero"
    # vault: orden registrada + episodio cerrado CONFIRMADO_PAGO_PENDIENTE + escalado PAYMENT_VERIFICATION_PENDING
    m = h.meta()
    assert m["registered_order"]["success"] is True and m["registered_order"]["order_id"] == "order_1"
    ep = m["episodes"][-1]
    assert ep["order_id"] == "order_1" and ep["closing_tag"] == "CONFIRMADO_PAGO_PENDIENTE" and ep["closed_at_ms"]
    assert body["episode_closed"] == {"episode_id": ep["episode_id"], "closing_tag": "CONFIRMADO_PAGO_PENDIENTE"}
    assert h.closed == [(_A, ep["episode_id"], "CONFIRMADO_PAGO_PENDIENTE")]
    assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "HUMANO"
    assert m["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING" and body["escalated"] is True
    tags = [e["tag"] for e in m["status_history"]]
    assert tags == ["CONFIRMADO_PAGO_PENDIENTE", "HUMANO"]
    # las instrucciones de pago quedaron encoladas antes del flush (transfer)
    assert m["pending_ui_intents"][0]["kind"] == "payment_instructions"


def test_order_is_idempotent_for_the_same_content(h: _Harness) -> None:
    first = h.client.post(_url("order"), json=_ORDER).json()
    second = h.client.post(_url("order"), json=_ORDER).json()
    assert second["registered"] is True and second["order_id"] == first["order_id"]
    assert second["already_registered"] is True
    assert len(h.port.calls) == 1 and h.flushed == [_A] and len(h.closed) == 1
    assert len(h.meta()["registered_orders_history"]) == 1


def test_order_with_cash_on_delivery_carries_the_minimum_rate_and_no_payment_intent(h: _Harness) -> None:
    body = h.client.post(_url("order"), json={**_ORDER, "payment_method": "cash_on_delivery",
                                               "shipping": {**_ORDER["shipping"], "city": "Medellín"}}).json()
    assert body["registered"] is True
    assert (body["subtotal_cop"], body["shipping_cop"], body["total_cop"]) == (58000, 16940, 74940)
    assert body["payment_instructions_sent"] is False and h.flushed == []
    assert "pending_ui_intents" not in h.meta() or h.meta()["pending_ui_intents"] == []


def test_order_reports_portavelas_from_the_catalog(h: _Harness) -> None:
    body = h.client.post(_url("order"), json={**_ORDER, "items": [
        {"handle": "duo-zodiacal", "variant_label": "Leo", "quantity": 1}]}).json()
    assert body["portavelas_included"] is True and body["portavelas_handles"] == ["duo-zodiacal"]


def test_order_with_unknown_product_or_bad_shipping_does_not_register(h: _Harness) -> None:
    body = h.client.post(_url("order"), json={**_ORDER, "items": [{"handle": "nope", "quantity": 1}]}).json()
    assert body["registered"] is False and body["error_detail"] == "unknown_product" and body["problems"] == ["unknown_product:nope"]
    assert h.port.calls == [] and not (h.vault / _A / "metadata.json").exists()
    r = h.client.post(_url("order"), json={**_ORDER, "shipping": {**_ORDER["shipping"], "receiver_name": " "}})
    assert r.status_code == 422
    r = h.client.post(_url("order"), json={**_ORDER, "items": []})
    assert r.status_code == 422
    r = h.client.post(_url("order"), json={**_ORDER, "payment_method": "bitcoin"})
    assert r.status_code == 422


def test_order_when_the_provider_fails_keeps_the_audit_and_does_not_close(h: _Harness) -> None:
    h.port.ok = False
    body = h.client.post(_url("order"), json=_ORDER).json()
    assert body["registered"] is False and body["order_id"] is None and body["error_detail"] == "down"
    m = h.meta()
    assert "registered_order" not in m and len(m["failed_order_registrations"]) == 1
    assert m.get("active_route") != ROUTE_HUMANO and h.flushed == [] and h.closed == []


def test_order_without_catalog_in_this_process_is_an_explicit_error(tmp_path: Path) -> None:
    async def flush(_s: str) -> int:
        return 0

    async def notify(*_a: Any) -> None:
        return None

    deps = SessionActionsDeps(vault_dir=tmp_path, catalog=None, order_port=_Port(), flush=flush, notify_episode_closed=notify)
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    body = TestClient(app).post(_url("order"), json=_ORDER).json()
    assert body["registered"] is False and body["error_detail"] == "catalog_unavailable"


# ── /tag ──────────────────────────────────────────────────────────────────────


def test_tag_interesado_keeps_the_episode_open_and_rechazo_closes_it(h: _Harness) -> None:
    h.client.post(_url("draft"), json={"producto": "luz-serena"})
    r = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "lo piensa"})
    assert r.status_code == 200 and r.json()["tag"] == "INTERESADO" and r.json()["episode_closed"] is None
    m = h.meta()
    assert m["tag"] == "INTERESADO" and m["motivo"] == "lo piensa" and m["episodes"][-1].get("closed_at_ms") is None
    assert h.closed == []
    r = h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": "no le interesa"})
    ep = h.meta()["episodes"][-1]
    assert r.json()["episode_closed"] == {"episode_id": ep["episode_id"], "closing_tag": "RECHAZO"}
    assert ep["closing_tag"] == "RECHAZO" and ep["closed_at_ms"]
    assert h.closed == [(_A, ep["episode_id"], "RECHAZO")]
    assert [e["tag"] for e in h.meta()["status_history"]] == ["INTERESADO", "RECHAZO"]
    # cerrar dos veces es idempotente: no se re-emite el evento
    assert h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": "otra vez"}).json()["episode_closed"] is None
    assert len(h.closed) == 1


def test_tag_accepts_only_the_two_proposals_mba_can_make(h: _Harness) -> None:
    """D1.3: MBA PROPONE (INTERESADO / RECHAZO); el estado de un pedido lo decide Hubara."""
    assert h.client.post(_url("tag"), json={"tag": "HUMANO", "motivo": "x"}).status_code == 422
    assert h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": ""}).status_code == 422
    for tag in ("CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS", "COMPRA_EXITOSA"):
        assert h.client.post(_url("tag"), json={"tag": tag, "motivo": "x"}).status_code == 422, tag


def test_tag_proposal_is_discarded_when_the_episode_has_a_registered_order(h: _Harness) -> None:
    """Orden registrada gana: la propuesta de MBA no pisa el estado real (un
    INTERESADO acá dispararía remarketing a un cliente que YA compró)."""
    h.client.post(_url("order"), json=_ORDER)
    # el colega verificó el pago y devolvió la conversación al bot
    meta_path = h.vault / _A / "metadata.json"
    m = json.loads(meta_path.read_text(encoding="utf-8"))
    m["active_route"], m["tag"] = "ventas", "COMPRA_EXITOSA"
    meta_path.write_text(json.dumps(m), encoding="utf-8")
    history_before = list(m["status_history"])
    r = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "se despidió"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tag"] == "COMPRA_EXITOSA" and body["proposed_tag"] == "INTERESADO"
    assert body["applied"] is False and body["reconciled"] is True and body["reason"] == "order_registered"
    assert body["episode_closed"] is None and body["escalated"] is False
    after = h.meta()
    assert after["tag"] == "COMPRA_EXITOSA" and after["status_history"] == history_before
    assert len(h.closed) == 1  # solo el cierre del /order


def test_tag_with_shipping_data_and_no_order_becomes_confirmado_sin_datos_and_escalates(h: _Harness) -> None:
    """Datos de envío sin orden = el cliente confirmó y no terminó: cierre
    CONFIRMADO_SIN_DATOS + escalación ORDER_PENDING_SHIPPING_DETAILS (la misma
    red de seguridad del workflow Sales), con la invariante handoff."""
    h.client.post(_url("draft"), json={"producto": "luz-serena", "ciudad": "Bogotá", "direccion": "Cl 1 # 2-3"})
    r = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "dejó de responder"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tag"] == "CONFIRMADO_SIN_DATOS" and body["proposed_tag"] == "INTERESADO"
    assert body["applied"] is True and body["reconciled"] is True and body["reason"] == "shipping_data_without_order"
    assert body["escalated"] is True
    m = h.meta()
    ep = m["episodes"][-1]
    assert body["episode_closed"] == {"episode_id": ep["episode_id"], "closing_tag": "CONFIRMADO_SIN_DATOS"}
    assert ep["closing_tag"] == "CONFIRMADO_SIN_DATOS" and ep["closed_at_ms"]
    assert "INTERESADO" in ep["closing_motivo"] and "dejó de responder" in ep["closing_motivo"]
    assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "HUMANO"
    assert m["escalation_reason"] == "ORDER_PENDING_SHIPPING_DETAILS"
    assert [e["tag"] for e in m["status_history"]] == ["CONFIRMADO_SIN_DATOS", "HUMANO"]
    assert h.closed == [(_A, ep["episode_id"], "CONFIRMADO_SIN_DATOS")]
    # retry de Meta tras el éxito: el colega ya tiene el hilo → 409 y el vault intacto
    snapshot = h.meta()
    assert h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "dejó de responder"}).status_code == 409
    assert h.meta() == snapshot and len(h.closed) == 1
    # con solo producto/color elegido NO hay confirmación: INTERESADO se aplica tal cual
    h.client.post(_url("draft", _B), json={"producto": "luz-serena", "color": "Blanco"})
    body = h.client.post(_url("tag", _B), json={"tag": "INTERESADO", "motivo": "lo piensa"}).json()
    assert body["tag"] == "INTERESADO" and body["applied"] is True and body["reconciled"] is False
    assert h.meta(_B).get("active_route") != ROUTE_HUMANO


def test_tag_is_idempotent_per_session_and_tag(h: _Harness) -> None:
    first = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "lo piensa"}).json()
    assert first["applied"] is True and first["reason"] == "proposal_accepted"
    second = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "lo piensa otra vez"}).json()
    assert second["tag"] == "INTERESADO" and second["applied"] is False and second["reason"] == "already_applied"
    m = h.meta()
    assert m["motivo"] == "lo piensa" and [e["tag"] for e in m["status_history"]] == ["INTERESADO"]


# ── /escalate ─────────────────────────────────────────────────────────────────


def test_escalate_marks_route_and_tag_together_and_is_idempotent_once_human(h: _Harness) -> None:
    r = h.client.post(_url("escalate"), json={"reason_category": "BULK_ORDER", "summary": "pide 30 unidades"})
    assert r.status_code == 200, r.text
    assert r.json() == {"escalated": True, "already_human": False, "active_route": ROUTE_HUMANO, "tag": "HUMANO"}
    m = h.meta()
    assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "HUMANO" and m["motivo"] == "pide 30 unidades"
    assert m["escalation_reason"] == "BULK_ORDER"
    assert m["status_history"][-1]["reason_category"] == "BULK_ORDER" and m["status_history"][-1]["source"] == "session_actions"
    # un humano ya tiene el hilo: no se pisa su estado
    r = h.client.post(_url("escalate"), json={"reason_category": "EXPLICIT_REQUEST", "summary": "otra"})
    assert r.json()["escalated"] is False and r.json()["already_human"] is True
    assert h.meta()["motivo"] == "pide 30 unidades" and len(h.meta()["status_history"]) == 1


def test_escalate_validates_inputs(h: _Harness) -> None:
    assert h.client.post(_url("escalate"), json={"reason_category": "bulk order", "summary": "x"}).status_code == 422
    assert h.client.post(_url("escalate"), json={"reason_category": "BULK_ORDER", "summary": ""}).status_code == 422


# ── scoping ───────────────────────────────────────────────────────────────────


def test_actions_are_scoped_to_their_session(h: _Harness) -> None:
    h.client.post(_url("draft", _A), json={"producto": "luz-serena"})
    h.client.post(_url("escalate", _B), json={"reason_category": "BULK_ORDER", "summary": "x"})
    assert h.meta(_A).get("active_route") != ROUTE_HUMANO
    assert "episodes" not in h.meta(_B) or not h.meta(_B)["episodes"][-1].get("order_draft")


def test_the_human_route_literal_matches_the_platform_constant() -> None:
    """chats no puede importar `src.platform.constants` en un archivo nuevo (P-28);
    el literal local debe seguir siendo el mismo que lee la bandeja humana."""
    assert session_actions.ROUTE_HUMANO == ROUTE_HUMANO


# ── invariante handoff con un humano en el hilo ──────────────────────────────


def test_writes_never_take_the_session_out_of_the_human_inbox(h: _Harness) -> None:
    """route=humano ⇔ tag=HUMANO: con un colega en el hilo, /tag se rechaza y
    /order cierra el episodio sin pisar el tag visible (la bandeja filtra por él)."""
    h.client.post(_url("escalate"), json={"reason_category": "EXPLICIT_REQUEST", "summary": "quiere humano"})
    r = h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": "x"})
    assert r.status_code == 409 and "already_human" in r.json()["detail"]
    assert h.meta()["tag"] == "HUMANO" and h.closed == []
    body = h.client.post(_url("order"), json=_ORDER).json()
    assert body["registered"] is True and body["escalated"] is False
    m = h.meta()
    assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "HUMANO"
    assert m["episodes"][-1]["closing_tag"] == "CONFIRMADO_PAGO_PENDIENTE" and body["episode_closed"]
    assert [e["tag"] for e in m["status_history"]] == ["HUMANO", "CONFIRMADO_PAGO_PENDIENTE"]


def test_the_same_order_in_a_new_episode_is_a_new_sale(h: _Harness) -> None:
    first = h.client.post(_url("order"), json=_ORDER).json()
    # el cliente vuelve semanas después: el draft abre un episodio nuevo
    h.client.post(_url("draft"), json={"producto": "luz-serena"})
    second = h.client.post(_url("order"), json=_ORDER).json()
    assert second["already_registered"] is False and second["order_id"] != first["order_id"]
    assert len(h.port.calls) == 2 and h.flushed == [_A, _A] and len(h.closed) == 2


def test_tag_decides_on_the_route_read_under_the_lock_not_on_a_stale_snapshot(h: _Harness, monkeypatch) -> None:
    """Carrera /tag × /order (revisión D1.3 H3): si un humano toma el hilo entre
    la llegada del request y la escritura, el chequeo de ruta tiene que verlo.
    Se simula flipeando la ruta DENTRO del read-modify-write del store."""
    from src.platform.state import FilesystemMetadataStore

    h.client.post(_url("draft"), json={"producto": "luz-serena"})
    original = FilesystemMetadataStore.update

    def update_with_human_taking_over(self, session_id, mutator):
        def wrapped(data):
            data["active_route"], data["tag"] = ROUTE_HUMANO, "HUMANO"
            return mutator(data)
        return original(self, session_id, wrapped)

    monkeypatch.setattr(FilesystemMetadataStore, "update", update_with_human_taking_over)
    before = h.meta()
    r = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "se despidió"})
    assert r.status_code == 409 and "already_human" in r.json()["detail"]
    assert h.meta() == before and h.closed == []


def test_session_locks_are_released_after_the_request(h: _Harness) -> None:
    h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "x"})
    h.client.post(_url("order"), json=_ORDER)
    assert _A not in session_actions._SESSION_LOCKS


@pytest.mark.asyncio
async def test_concurrent_tag_and_order_never_break_the_handoff_invariant(tmp_path: Path) -> None:
    """MBA puede emitir register_order y manage_conversation_tag en el mismo turno.
    En cualquier orden de llegada, el estado final es el del pedido: route=humano
    y tag=HUMANO. Guard del estado final (con ASGITransport las secciones críticas
    no se intercalan); la carrera real la cubre el test de ruta bajo el lock."""
    import httpx

    async def flush(_: str) -> int:
        return 1

    async def notify(*_: Any) -> None:
        return None

    deps = SessionActionsDeps(vault_dir=tmp_path, catalog=_Catalog(), order_port=_Port(), flush=flush, notify_episode_closed=notify)
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    for first in ("tag", "order"):
        session = _A if first == "tag" else _B
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            calls = {
                "tag": client.post(_url("tag", session), json={"tag": "INTERESADO", "motivo": "se despidió"}),
                "order": client.post(_url("order", session), json=_ORDER),
            }
            responses = await asyncio.gather(calls[first], calls["order" if first == "tag" else "tag"])
        assert {r.status_code for r in responses} <= {200, 409}
        m = json.loads((tmp_path / session / "metadata.json").read_text(encoding="utf-8"))
        assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "HUMANO", (first, m["status_history"])
        assert m["episodes"][-1]["closing_tag"] == "CONFIRMADO_PAGO_PENDIENTE"


@pytest.mark.asyncio
async def test_concurrent_identical_orders_register_and_send_payment_instructions_once(tmp_path: Path) -> None:
    import httpx

    port = _Port()
    flushed: list[str] = []

    async def flush(session_key: str) -> int:
        await asyncio.sleep(0.01)
        flushed.append(session_key)
        return 1

    async def notify(*_a: Any) -> None:
        await asyncio.sleep(0.01)

    class _SlowPort(_Port):
        async def register_order(self, **kw: Any) -> OrderRegistrationResult:
            await asyncio.sleep(0.05)  # ventana para que la 2ª request pase el pre-check
            return await super().register_order(**kw)

    port = _SlowPort()
    deps = SessionActionsDeps(vault_dir=tmp_path, catalog=_Catalog(), order_port=port, flush=flush, notify_episode_closed=notify)
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        a, b = await asyncio.gather(c.post(_url("order"), json=_ORDER), c.post(_url("order"), json=_ORDER))
    assert a.json()["order_id"] == b.json()["order_id"] == "order_1"
    assert sorted([a.json()["already_registered"], b.json()["already_registered"]]) == [False, True]
    assert len(port.calls) == 1 and flushed == [_A]


def test_tag_interesado_via_api_enqueues_qualified_lead_for_ctwa_sessions(h: _Harness) -> None:
    """Auditoría CAPI 2026-09-08: el endpoint ya no pasa por la tool, así que
    la señal de embudo (INTERESADO → QualifiedLead) se encola en `_apply_tag`."""
    h.client.post(_url("draft"), json={"producto": "luz-serena"})
    meta = h.meta()
    meta["ctwa_referrals"] = [{"ctwa_clid": "CLID_API", "captured_at_ms": 1_757_350_000_000}]
    (h.vault / _A / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    r = h.client.post(_url("tag"), json={"tag": "INTERESADO", "motivo": "lo piensa"})
    assert r.status_code == 200
    ep = h.meta()["episodes"][-1]["episode_id"]
    outbox = h.meta().get("capi_outbox", [])
    assert [(e["event_name"], e["event_id"], e["source"]) for e in outbox] == [
        ("QualifiedLead", f"qualifiedlead_{_A}_{ep}", "session_actions:tag")
    ]


# ── /operator-tag (botón "Reasignar" del inspector) ──────────────────────────


def test_operator_tag_applies_the_human_decision_without_reconciling(h: _Harness) -> None:
    """El operador DECIDE (no propone): RECHAZO cierra el episodio, INTERESADO lo
    reabre como visible, y queda trazado en status_history con source dashboard."""
    h.client.post(_url("draft"), json={"producto": "luz-serena"})
    r = h.client.post(_url("operator-tag"), json={"tag": "RECHAZO", "motivo": "buscaba cera, no la vendemos"})
    assert r.status_code == 200, r.text
    ep = h.meta()["episodes"][-1]
    assert r.json() == {
        "tag": "RECHAZO", "motivo": "buscaba cera, no la vendemos", "active_route": "ventas",
        "episode_closed": {"episode_id": ep["episode_id"], "closing_tag": "RECHAZO"},
    }
    m = h.meta()
    assert m["tag"] == "RECHAZO" and m["motivo"] == "buscaba cera, no la vendemos"
    assert ep["closing_tag"] == "RECHAZO" and ep["closed_at_ms"]
    assert h.closed == [(_A, ep["episode_id"], "RECHAZO")]
    last = m["status_history"][-1]
    assert last["tag"] == "RECHAZO" and last["source"] == "dashboard:operator"


def test_operator_tag_remarketing_marks_the_lead_for_reactivation(h: _Harness) -> None:
    """REMARKETING = decisión humana de re-contactar (la central send_policy la
    respeta aunque el último cierre haya sido RECHAZO). No cierra episodio."""
    h.client.post(_url("operator-tag"), json={"tag": "RECHAZO", "motivo": "no"})
    r = h.client.post(_url("operator-tag"), json={"tag": "REMARKETING", "motivo": "el cliente pidió que le escribamos"})
    assert r.status_code == 200 and r.json()["tag"] == "REMARKETING" and r.json()["episode_closed"] is None
    assert h.meta()["tag"] == "REMARKETING"


def test_operator_tag_rejects_tags_the_operator_must_not_set_by_hand(h: _Harness) -> None:
    for tag in ("HUMANO", "COMPRA_EXITOSA", "CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS", "NO_ETIQUETADO"):
        assert h.client.post(_url("operator-tag"), json={"tag": tag, "motivo": "x"}).status_code == 422, tag
    assert h.client.post(_url("operator-tag"), json={"tag": "RECHAZO", "motivo": ""}).status_code == 422


def test_operator_tag_keeps_the_human_route_untouched(h: _Harness) -> None:
    """Con un humano en el hilo el operador puede etiquetar igual (es él quien
    decide); la ruta no se toca — devolver al bot es otra acción."""
    h.client.post(_url("escalate"), json={"reason_category": "EXPLICIT_REQUEST", "summary": "pidió humano"})
    r = h.client.post(_url("operator-tag"), json={"tag": "INTERESADO", "motivo": "quedó pensando el envío"})
    assert r.status_code == 200 and r.json()["active_route"] == ROUTE_HUMANO
    m = h.meta()
    assert m["active_route"] == ROUTE_HUMANO and m["tag"] == "INTERESADO"


# ── D1.10: cada connector tool resuelve el episodio ACTIVO ───────────────────


def test_after_a_close_the_draft_starts_empty_in_the_new_episode_and_the_old_one_keeps_its_own(h: _Harness) -> None:
    """Dos episodios, dos drafts: tras un cierre (RECHAZO), `set_order_slot`
    escribe en un draft vacío del episodio nuevo; el draft del episodio
    cerrado queda intacto. MBA no puede actuar sobre datos del episodio
    anterior aunque los recuerde."""
    assert h.client.post(_url("draft"), json={"producto": "luz-serena", "cantidad": 2}).status_code == 200
    r = h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": "no le interesó"})
    assert r.status_code == 200 and r.json()["episode_closed"]["closing_tag"] == "RECHAZO"
    closed_id = r.json()["episode_closed"]["episode_id"]

    r = h.client.post(_url("draft"), json={"ciudad": "Cali"})
    assert r.status_code == 200, r.text
    meta = h.meta()
    episodes = meta["episodes"]
    assert episodes[-2]["episode_id"] == closed_id and episodes[-2]["closed_at_ms"] is not None
    assert episodes[-1]["episode_id"] != closed_id and episodes[-1]["closed_at_ms"] is None
    assert r.json()["order_draft"] == {"ciudad": "Cali"}  # draft NUEVO: sin producto/cantidad del episodio cerrado
    assert episodes[-1]["order_draft"]["slots"] == {"ciudad": "Cali"}
    assert episodes[-2]["order_draft"]["slots"] == {"producto": "luz-serena", "cantidad": "2"}  # intacto
    assert meta["tag"] == "NO_ETIQUETADO"
