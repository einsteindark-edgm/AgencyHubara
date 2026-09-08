"""Tools de escritura de MBA (D1.2b): mapeo al contrato `session-actions@v1` de chats.

`mba` no importa chats (P-3): cada tool valida sus listas cerradas, traduce los
parámetros que Meta envía al body del contrato y devuelve al agente un
envelope estable (los fallos del cast son errores explícitos, nunca 500).
"""
from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import HTTPException

from src.plugins.mba.tools import session

_S = "wa_573001234567"


class _Chats:
    def __init__(self, response: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._response = response or {}
        self._exc = exc

    async def __call__(self, request: Any, session_key: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((session_key, action, body))
        if self._exc is not None:
            raise self._exc
        return dict(self._response)


async def test_set_order_slot_forwards_the_slots_as_is() -> None:
    chats = _Chats({"updated": True, "order_draft": {"producto": "luz-serena", "cantidad": "2"}})
    out = await session.set_order_slot(chats, None, session_key=_S, params={"producto": "luz-serena", "cantidad": 2})
    assert chats.calls == [(_S, "draft", {"producto": "luz-serena", "cantidad": 2})]
    assert out["order_draft"]["producto"] == "luz-serena"


@pytest.mark.parametrize("raw,expected", [
    ("contra_entrega", "cash_on_delivery"), ("Contra entrega", "cash_on_delivery"), ("contraentrega", "cash_on_delivery"),
    ("anticipado", "transfer"), ("pago anticipado (Nequi)", "transfer"), ("Nequi", "transfer"), ("transferencia", "transfer"),
    ("link_de_pago", "payment_link"), ("Link de pago", "payment_link"),
])
def test_payment_method_normalization(raw: str, expected: str) -> None:
    assert session.normalize_payment_method(raw) == expected


async def test_register_order_maps_meta_params_to_the_chats_body_and_applies_rule_241() -> None:
    chats = _Chats({
        "registered": True, "already_registered": False, "order_id": "order_1", "order_reference": "#22 (Luz Serena)",
        "provider": "medusa", "payment_method": "transfer", "currency": "COP",
        "subtotal_cop": 58000, "shipping_cop": 7900, "total_cop": 65900,
        "items": [{"handle": "luz-serena", "variant_label": "Lavanda, Blanco", "quantity": 2, "unit_price_cop": 29000,
                   "title": "Luz Serena", "variant_resolved": True}],
        "portavelas_included": False, "portavelas_handles": [], "episode_closed": {"episode_id": "ep", "closing_tag": "X"},
        "escalated": True, "payment_instructions_sent": True,
    })
    params = {
        "items": [{"handle": "luz-serena", "variant_label": "Lavanda, Blanco", "quantity": 2}],
        "ciudad": "Bogotá", "barrio": "Chapinero", "direccion": "Cl 1 # 2-3", "telefono": "3001234567",
        "nombre_recibe": "Ana Pérez", "cedula": "123", "metodo_pago": "anticipado",
    }
    out = await session.register_order(chats, None, session_key=_S, params=params)
    assert chats.calls == [(_S, "order", {
        "items": [{"handle": "luz-serena", "variant_label": "Lavanda, Blanco", "quantity": 2}],
        "shipping": {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Cl 1 # 2-3", "phone": "3001234567",
                     "receiver_name": "Ana Pérez", "national_id": "123"},
        "payment_method": "transfer",
    })]
    # anticipado: envío = tarifa mínima + total (#241)
    assert out["registered"] is True and out["order_id"] == "order_1" and out["order_reference"] == "#22 (Luz Serena)"
    assert (out["subtotal_cop"], out["shipping_cop"], out["total_cop"]) == (58000, 7900, 65900)
    assert out["shipping_is_minimum_rate"] is True and out["portavelas_included"] is False
    assert out["payment_instructions_sent"] is True
    assert "episode_closed" not in out and "escalated" not in out and "provider" not in out  # interno de chats
    assert "registrado" in out["message"]


async def test_register_order_with_cash_on_delivery_never_returns_shipping_or_total() -> None:
    chats = _Chats({"registered": True, "already_registered": True, "order_id": "order_1", "order_reference": None,
                    "payment_method": "cash_on_delivery", "subtotal_cop": 58000, "shipping_cop": 16940, "total_cop": 74940,
                    "items": [], "portavelas_included": True, "portavelas_handles": ["duo-zodiacal"],
                    "payment_instructions_sent": False})
    params = {"items": [{"handle": "duo-zodiacal", "quantity": 1}], "ciudad": "Medellín", "direccion": "x",
              "telefono": "3001234567", "nombre_recibe": "Ana", "metodo_pago": "contra_entrega"}
    out = await session.register_order(chats, None, session_key=_S, params=params)
    body = chats.calls[0][2]
    assert body["shipping"] == {"city": "Medellín", "neighborhood": "", "address": "x", "phone": "3001234567",
                                "receiver_name": "Ana", "national_id": None}
    assert body["payment_method"] == "cash_on_delivery"
    assert out["subtotal_cop"] == 58000 and "shipping_cop" not in out and "total_cop" not in out
    assert "transportadora" in out["shipping_note"] and out["portavelas_included"] is True
    assert out["already_registered"] is True


async def test_register_order_rejects_unknown_payment_method_without_calling_chats() -> None:
    chats = _Chats()
    out = await session.register_order(chats, None, session_key=_S, params={
        "items": [{"handle": "x", "quantity": 1}], "ciudad": "c", "direccion": "d", "telefono": "3001234567",
        "nombre_recibe": "Ana", "metodo_pago": "bitcoin"})
    assert out["error"] == "invalid_payment_method" and chats.calls == []
    assert set(out["accepted"]) == {"contra_entrega", "anticipado", "link_de_pago"}


async def test_register_order_not_registered_tells_the_agent_to_escalate() -> None:
    chats = _Chats({"registered": False, "order_id": None, "error_detail": "unknown_product", "problems": ["unknown_product:x"]})
    out = await session.register_order(chats, None, session_key=_S, params={
        "items": [{"handle": "x", "quantity": 1}], "ciudad": "c", "direccion": "d", "telefono": "3001234567",
        "nombre_recibe": "Ana", "metodo_pago": "anticipado"})
    assert out["registered"] is False and out["error_detail"] == "unknown_product"
    assert "ORDER_REGISTRATION_FAILED" in out["message"]


async def test_manage_conversation_tag_only_proposes_interesado_or_rechazo() -> None:
    chats = _Chats({"tag": "RECHAZO", "motivo": "m", "message": "ok", "episode_closed": {"episode_id": "e", "closing_tag": "RECHAZO"}})
    out = await session.manage_conversation_tag(chats, None, session_key=_S, params={"tag": "RECHAZO", "motivo": "m"})
    assert chats.calls == [(_S, "tag", {"tag": "RECHAZO", "motivo": "m"})] and out["tag"] == "RECHAZO"
    out = await session.manage_conversation_tag(chats, None, session_key=_S, params={"tag": "COMPRA_EXITOSA", "motivo": "m"})
    assert out["error"] == "invalid_tag" and out["accepted"] == ["INTERESADO", "RECHAZO"] and len(chats.calls) == 1


async def test_manage_conversation_tag_tells_the_agent_when_hubara_applied_another_tag() -> None:
    """D1.3: la propuesta puede reconciliarse; el agente lee qué quedó aplicado y no insiste."""
    chats = _Chats({"tag": "CONFIRMADO_SIN_DATOS", "proposed_tag": "INTERESADO", "applied": True, "reconciled": True,
                    "reason": "shipping_data_without_order", "escalated": True,
                    "episode_closed": {"episode_id": "e", "closing_tag": "CONFIRMADO_SIN_DATOS"}})
    out = await session.manage_conversation_tag(chats, None, session_key=_S, params={"tag": "interesado", "motivo": "m"})
    assert chats.calls == [(_S, "tag", {"tag": "INTERESADO", "motivo": "m"})]
    assert out["tag"] == "CONFIRMADO_SIN_DATOS" and out["proposed_tag"] == "INTERESADO" and out["reconciled"] is True
    assert "CONFIRMADO_SIN_DATOS" in out["message"] and "colega" in out["message"]
    # descartada por orden registrada: el estado no cambió, el agente no vuelve a etiquetar
    chats = _Chats({"tag": "COMPRA_EXITOSA", "proposed_tag": "RECHAZO", "applied": False, "reconciled": True,
                    "reason": "order_registered", "escalated": False, "episode_closed": None})
    out = await session.manage_conversation_tag(chats, None, session_key=_S, params={"tag": "RECHAZO", "motivo": "m"})
    assert out["applied"] is False and "pedido registrado" in out["message"] and "no vuelvas" in out["message"].lower()
    # aplicada tal cual: mensaje corto de confirmación
    chats = _Chats({"tag": "INTERESADO", "proposed_tag": "INTERESADO", "applied": True, "reconciled": False,
                    "reason": "proposal_accepted", "escalated": False, "episode_closed": None})
    out = await session.manage_conversation_tag(chats, None, session_key=_S, params={"tag": "INTERESADO", "motivo": "m"})
    assert out["reconciled"] is False and "INTERESADO" in out["message"]


async def test_escalate_to_human_validates_the_reason_category() -> None:
    chats = _Chats({"escalated": True, "already_human": False, "active_route": "humano", "tag": "HUMANO"})
    out = await session.escalate_to_human(chats, None, session_key=_S, params={"reason_category": "BULK_ORDER", "summary": "30 uds"})
    assert chats.calls == [(_S, "escalate", {"reason_category": "BULK_ORDER", "summary": "30 uds"})]
    assert out["escalated"] is True
    out = await session.escalate_to_human(chats, None, session_key=_S, params={"reason_category": "OTHER", "summary": "x"})
    assert out["error"] == "invalid_reason_category" and "BULK_ORDER" in out["accepted"] and len(chats.calls) == 1


def test_reason_categories_match_the_contract_registered_in_meta() -> None:
    """La lista cerrada de la tool = la que dice el request_definition autorado (un solo contrato)."""
    from src.plugins.mba.domain.tool_calls import contracts_from_config
    from src.plugins.mba.service import load_agent

    contract = contracts_from_config(load_agent("sales"))["escalate_to_human"]
    text = contract.params["reason_category"]["description"]
    declared = re.findall(r"[A-Z][A-Z0-9_]{2,}", text.split("Uno de:", 1)[1])
    assert declared == list(session.REASON_CATEGORIES)


@pytest.mark.parametrize("status,expected", [
    (502, {"error": "chats_unavailable", "applied": False}),
    (504, {"error": "chats_timeout", "applied": "unknown"}),
    (401, {"error": "rejected", "status": 401, "applied": False}),
    (409, {"error": "rejected", "status": 409, "applied": False}),
    (500, {"error": "chats_error", "status": 500, "applied": "unknown"}),
])
async def test_cast_failures_become_explicit_error_envelopes(status: int, expected: dict[str, Any]) -> None:
    chats = _Chats(exc=HTTPException(status_code=status, detail="cast mba→chats: algo"))
    out = await session.escalate_to_human(chats, None, session_key=_S, params={"reason_category": "BULK_ORDER", "summary": "x"})
    assert {k: out[k] for k in expected} == expected
    assert out["message"]
    # el detail del provider solo viaja para 409/422 (validación); el de auth no
    assert ("detail" in out) == (status in (409, 422))
