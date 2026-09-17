"""Tests de `_compute_order_ref` (dashboard chats) + su wiring en el listado.

La bandeja del dashboard no distinguía una conversación que YA se convirtió en
pedido de una que sigue negociando: ambas se ven igual en el filtro "Asignadas
al humano" (un chat queda en manos del humano por muchos motivos después de
cerrar la venta). El chip de la fila necesita saber, por sesión, si hay pedido
y en qué punto está el pago.

Todo sale del `metadata.json` que el listado YA lee — cero llamadas a Medusa
por fila (el estado logístico fino sigue viviendo en el panel de pedidos).

Fuentes de verdad de cada marca:
  * `registered_order` (`success`, `order_id`, `raw_payload.display_id`) lo
    escribe `register_order` al cerrar la venta.
  * `episodes[].payment_confirmed_at_ms` lo escribe / lo borra
    `apply_payment_confirmation_to_chat_metadata` y su reversa
    (`platform/orders/medusa_order_command.py`).
  * `episodes[].cancelled_at_ms` lo escribe la cancelación desde orders.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot
import src.plugins.chats.api.dashboard as dash_mod
from src.plugins.chats.api.dashboard import _compute_order_ref

ORDER_ID = "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z"


def _session_with_order(**overrides) -> dict:
    """Venta registrada y escalada para que un humano verifique el pago."""
    data = {
        "active_route": "humano",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "tag": "HUMANO",
        "registered_order": {
            "order_id": ORDER_ID,
            "success": True,
            "raw_payload": {"display_id": 31},
        },
        "episodes": [{"order_id": ORDER_ID, "closing_tag": "CONFIRMADO_PAGO_PENDIENTE"}],
        "registered_orders_history": [
            {"order_id": ORDER_ID, "success": True, "ts_ms": 1780090000000}
        ],
    }
    data.update(overrides)
    return data


# --- ¿hay pedido? ----------------------------------------------------------


def test_registered_order_exposes_display_id_and_order_id():
    ref = _compute_order_ref(_session_with_order())
    assert ref is not None
    assert ref["display_id"] == "31"
    assert ref["order_id"] == ORDER_ID


def test_no_registered_order_returns_none():
    assert _compute_order_ref({"tag": "INTERESADO"}) is None


def test_failed_registration_returns_none():
    """Registro fallido NO es un pedido — pintar el chip sería mentir."""
    assert _compute_order_ref(
        _session_with_order(
            registered_order={"order_id": ORDER_ID, "success": False}
        )
    ) is None


def test_missing_display_id_keeps_order_id_with_null_display():
    """Provider sin `display_id` (stub): el frontend cae al id corto."""
    ref = _compute_order_ref(
        _session_with_order(
            registered_order={"order_id": ORDER_ID, "success": True}
        )
    )
    assert ref is not None
    assert ref["display_id"] is None
    assert ref["order_id"] == ORDER_ID


# --- estado del pago -------------------------------------------------------


def test_escalated_sale_is_payment_pending():
    assert _compute_order_ref(_session_with_order())["payment"] == "pending"


def test_episode_marked_confirmed_is_payment_confirmed():
    ref = _compute_order_ref(
        _session_with_order(
            episodes=[
                {"order_id": ORDER_ID, "payment_confirmed_at_ms": 1780090366836}
            ],
        )
    )
    assert ref["payment"] == "confirmed"


def test_tag_compra_exitosa_is_payment_confirmed():
    """Metadata legacy sin la marca del episodio: el tag terminal alcanza."""
    ref = _compute_order_ref(
        _session_with_order(tag="COMPRA_EXITOSA", episodes=[])
    )
    assert ref["payment"] == "confirmed"


def test_reversed_payment_goes_back_to_pending():
    """La reversa borra `payment_confirmed_at_ms` y deja `payment_reversed_at_ms`."""
    ref = _compute_order_ref(
        _session_with_order(
            tag="HUMANO",
            episodes=[
                {
                    "order_id": ORDER_ID,
                    "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                    "payment_reversed_at_ms": 1780090999999,
                }
            ],
        )
    )
    assert ref["payment"] == "pending"


def test_cancelled_episode_is_cancelled():
    ref = _compute_order_ref(
        _session_with_order(
            tag="RECHAZO",
            episodes=[{"order_id": ORDER_ID, "cancelled_at_ms": 1780090366836}],
        )
    )
    assert ref["payment"] == "cancelled"


def test_tag_rechazo_is_cancelled():
    ref = _compute_order_ref(_session_with_order(tag="RECHAZO", episodes=[]))
    assert ref["payment"] == "cancelled"


def test_confirmed_episode_wins_over_rechazo_tag():
    """Cancelar después de confirmar NO revierte el pago (la orden cancela del
    lado de Medusa, pero el pago de ESTE pedido siguió confirmado)."""
    ref = _compute_order_ref(
        _session_with_order(
            tag="RECHAZO",
            episodes=[
                {"order_id": ORDER_ID, "payment_confirmed_at_ms": 1780090366836}
            ],
        )
    )
    assert ref["payment"] == "confirmed"


# --- varios pedidos en la misma sesión -------------------------------------


def test_counts_successful_orders_of_the_session():
    ref = _compute_order_ref(
        _session_with_order(
            registered_orders_history=[
                {"order_id": "order_OLD", "success": True, "ts_ms": 1},
                {"order_id": ORDER_ID, "success": True, "ts_ms": 2},
            ],
        )
    )
    assert ref["count"] == 2


def test_failed_attempts_do_not_inflate_the_count():
    ref = _compute_order_ref(
        _session_with_order(
            registered_orders_history=[
                {"order_id": "AUDIT-x", "success": False, "ts_ms": 1},
                {"order_id": ORDER_ID, "success": True, "ts_ms": 2},
            ],
        )
    )
    assert ref["count"] == 1


def test_missing_history_counts_one():
    data = _session_with_order()
    del data["registered_orders_history"]
    assert _compute_order_ref(data)["count"] == 1


# --- wiring: el listado EMITE el campo -------------------------------------


@pytest.fixture
def client_and_vault(tmp_path):
    app = FastAPI()
    app.include_router(dash_mod.router, prefix="/api/dashboard")
    with patch.object(dash_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed(vault, session_id: str, metadata: dict) -> None:
    path = vault / session_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def test_sessions_list_emits_order_ref(client_and_vault):
    """Gotcha #1: que el schema lo permita no basta — el listado debe EMITIRLO."""
    client, vault = client_and_vault
    _seed(vault, "wa_573001112233", _session_with_order())

    resp = client.get("/api/dashboard/sessions")

    assert resp.status_code == 200
    session = resp.json()["sessions"][0]
    assert session["order_ref"] == {
        "order_id": ORDER_ID,
        "display_id": "31",
        "payment": "pending",
        "count": 1,
    }


def test_sessions_list_emits_null_without_order(client_and_vault):
    client, vault = client_and_vault
    _seed(vault, "wa_573007654321", {"tag": "INTERESADO", "active_route": "ventas"})

    resp = client.get("/api/dashboard/sessions")

    assert resp.json()["sessions"][0]["order_ref"] is None


# --- el chip cuenta lo mismo que el botón (OrderFacts, #289) ---------------
#
# El botón "Confirmar pago" lo decide el PEDIDO desde #289
# (`test_dashboard_pending_payment_facts.py`). Si el chip siguiera decidiendo
# por las marcas del chat, la MISMA fila diría dos cosas: el operador cobra en
# Medusa Admin, el botón desaparece y el chip sigue en "pago por verificar".
# El snapshot que ya se lee para el botón resuelve también el chip — sin pedir
# un solo id extra a Medusa.


def _facts(pay: str = "paid", stage: str = "preparing") -> OrderFactsSnapshot:
    return OrderFactsSnapshot(
        facts={
            ORDER_ID: OrderFacts(
                order_id=ORDER_ID, display_id="31", total_cop=50000,
                currency_code="cop", pay_status=pay, stage=stage,
                customer="Ana", is_draft=False, created_at_ms=1,
            )
        }
    )


def test_paid_in_medusa_wins_over_the_chat_marks():
    """Cobrado desde Medusa Admin: el chat sigue diciendo HUMANO."""
    ref = _compute_order_ref(_session_with_order(), _facts())
    assert ref["payment"] == "confirmed"


def test_refunded_in_medusa_goes_back_to_pending():
    session = _session_with_order(tag="COMPRA_EXITOSA")
    session["episodes"][0]["payment_confirmed_at_ms"] = 1_700_000_000_000
    ref = _compute_order_ref(session, _facts(pay="refund"))
    assert ref["payment"] == "pending"


def test_cancelled_in_medusa_wins_over_the_chat_marks():
    ref = _compute_order_ref(_session_with_order(tag="COMPRA_EXITOSA"), _facts(stage="cancelled"))
    assert ref["payment"] == "cancelled"


def test_the_real_display_id_wins_over_the_frozen_one():
    session = _session_with_order()
    session["registered_order"]["raw_payload"] = {"display_id": 999}
    assert _compute_order_ref(session, _facts())["display_id"] == "31"


def test_order_outside_the_snapshot_falls_back_to_the_chat_marks():
    """Medusa caído o pedido fuera del batch: vale la regla vieja."""
    snapshot = OrderFactsSnapshot(unresolved=frozenset({ORDER_ID}), stale=True)
    assert _compute_order_ref(_session_with_order(), snapshot)["payment"] == "pending"
