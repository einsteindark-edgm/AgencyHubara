"""Contrato HTTP `session-actions@v1` de chats (D1.2b).

Lo consume el plugin `mba` por cast (canal 3) para las 4 tools de escritura
que Meta Business Agent invoca: `set_order_slot` → /draft, `register_order` →
/order, `manage_conversation_tag` → /tag, `escalate_to_human` → /escalate.
Cada endpoint resuelve el episodio ACTIVO de la sesión y deja en el vault
exactamente lo que dejarían las tools del agente Sales (gotcha 1: se verifica
el metadata.json real, no el schema).
"""
from __future__ import annotations

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


def test_tag_validates_the_closed_list_and_the_payment_pending_precondition(h: _Harness) -> None:
    assert h.client.post(_url("tag"), json={"tag": "HUMANO", "motivo": "x"}).status_code == 422
    assert h.client.post(_url("tag"), json={"tag": "RECHAZO", "motivo": ""}).status_code == 422
    r = h.client.post(_url("tag"), json={"tag": "CONFIRMADO_PAGO_PENDIENTE", "motivo": "x"})
    assert r.status_code == 409 and "register_order" in r.json()["detail"]


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
