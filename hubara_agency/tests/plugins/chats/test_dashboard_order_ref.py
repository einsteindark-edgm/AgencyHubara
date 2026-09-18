"""Tests de `_compute_order_ref` (dashboard chats) + su wiring en el listado.

El chip de la fila de la bandeja dice a qué ORDEN pertenece una conversación.
Solo aparece cuando el pedido YA es una orden real de Medusa con su número:

  * un **draft** (el bot registró la venta pero nadie agendó la entrega — que
    es lo que lo convierte en orden) todavía NO es una orden: sin chip. La
    primera versión lo pintaba igual, con un id interno inservible
    ("#B3XY9Z") en vez de un número de orden.
  * el número viaja PELADO ("32"): `OrderFacts.display_id` ya trae "#32" y el
    frontend le sumaba otro → "##32".

Fuente de verdad = `OrderFacts` (regla 13 del repo): draft/orden, número,
pago y etapa salen del mismo store que alimenta la vista Orders, en UNA
lectura para toda la bandeja. El vault solo aporta el vínculo
conversación → `order_id`. Sin dato del pedido (Medusa caído, id desconocido)
no se pinta nada: mejor sin chip que con un chip inventado.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot
import src.plugins.chats.api.dashboard as dash_mod
from src.plugins.chats.api.dashboard import (
    _compute_order_ref,
    _order_ref_candidate_ids,
)

ORDER_ID = "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z"
OLD_ORDER_ID = "order_01KAAAAAAAAAAAAAAAAAAAAOLD"


def _session_with_order(**overrides) -> dict:
    data = {
        "active_route": "humano",
        "tag": "HUMANO",
        "registered_order": {"order_id": ORDER_ID, "success": True},
        "registered_orders_history": [
            {"order_id": ORDER_ID, "success": True, "ts_ms": 1780090000000}
        ],
    }
    data.update(overrides)
    return data


def _fact(
    order_id: str = ORDER_ID,
    *,
    display: str = "#31",
    pay: str = "pending",
    stage: str = "preparing",
    is_draft: bool = False,
) -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id=display, total_cop=50000,
        currency_code="cop", pay_status=pay, stage=stage,
        customer="Ana", is_draft=is_draft, created_at_ms=1,
    )


def _snap(*facts: OrderFacts) -> OrderFactsSnapshot:
    return OrderFactsSnapshot(facts={f.order_id: f for f in facts})


# --- ¿ya es una orden? -----------------------------------------------------


def test_real_order_exposes_its_number():
    ref = _compute_order_ref(_session_with_order(), _snap(_fact()))
    assert ref is not None
    assert ref["order_id"] == ORDER_ID


def test_draft_is_not_an_order_yet():
    """El bot registró la venta pero nadie agendó: sigue siendo un draft."""
    assert _compute_order_ref(
        _session_with_order(), _snap(_fact(is_draft=True))
    ) is None


def test_display_id_travels_without_the_hash():
    """`OrderFacts.display_id` ya trae "#31" — el frontend pintaba "##31"."""
    ref = _compute_order_ref(_session_with_order(), _snap(_fact(display="#31")))
    assert ref["display_id"] == "31"


def test_order_without_a_real_number_has_no_chip():
    """Sin `display_id` numérico el mapper de Medusa cae a "#<últimos 6 del
    id>": un id interno que al operador no le sirve de nada."""
    assert _compute_order_ref(
        _session_with_order(), _snap(_fact(display="#B3XY9Z"))
    ) is None


def test_no_registered_order_returns_none():
    assert _compute_order_ref({"tag": "INTERESADO"}, _snap()) is None


def test_failed_registration_returns_none():
    assert _compute_order_ref(
        _session_with_order(
            registered_order={"order_id": ORDER_ID, "success": False}
        ),
        _snap(_fact()),
    ) is None


def test_order_gone_from_medusa_has_no_chip():
    assert _compute_order_ref(_session_with_order(), _snap()) is None


def test_unresolved_order_has_no_chip():
    """Medusa caído y nunca visto: no sabemos si es draft u orden. Sin chip."""
    snapshot = OrderFactsSnapshot(unresolved=frozenset({ORDER_ID}), stale=True)
    assert _compute_order_ref(_session_with_order(), snapshot) is None


def test_without_a_snapshot_there_is_no_chip():
    assert _compute_order_ref(_session_with_order(), None) is None


# --- estado del pago: lo decide el PEDIDO ----------------------------------


def test_unpaid_order_is_payment_pending():
    ref = _compute_order_ref(_session_with_order(), _snap(_fact(pay="pending")))
    assert ref["payment"] == "pending"


def test_paid_order_is_confirmed_even_if_the_chat_tag_lags():
    """Cobrado desde Medusa Admin: el chat sigue diciendo HUMANO."""
    ref = _compute_order_ref(_session_with_order(), _snap(_fact(pay="paid")))
    assert ref["payment"] == "confirmed"


def test_refunded_order_goes_back_to_pending():
    ref = _compute_order_ref(
        _session_with_order(tag="COMPRA_EXITOSA"), _snap(_fact(pay="refund"))
    )
    assert ref["payment"] == "pending"


def test_cancelled_order_is_cancelled():
    ref = _compute_order_ref(
        _session_with_order(), _snap(_fact(pay="paid", stage="cancelled"))
    )
    assert ref["payment"] == "cancelled"


# --- varios pedidos en la misma sesión -------------------------------------


def _two_order_session() -> dict:
    return _session_with_order(
        registered_orders_history=[
            {"order_id": OLD_ORDER_ID, "success": True, "ts_ms": 1},
            {"order_id": "AUDIT-x", "success": False, "ts_ms": 2},
            {"order_id": ORDER_ID, "success": True, "ts_ms": 3},
        ],
    )


def test_counts_the_real_orders_of_the_session():
    ref = _compute_order_ref(
        _two_order_session(), _snap(_fact(), _fact(OLD_ORDER_ID, display="#28"))
    )
    assert ref["count"] == 2


def test_older_drafts_do_not_inflate_the_count():
    ref = _compute_order_ref(
        _two_order_session(),
        _snap(_fact(), _fact(OLD_ORDER_ID, display="#28", is_draft=True)),
    )
    assert ref["count"] == 1


def test_candidate_ids_are_the_successful_orders_of_the_session():
    """Los ids que el listado junta para leer `OrderFacts` en UN batch."""
    assert _order_ref_candidate_ids(_two_order_session()) == {ORDER_ID, OLD_ORDER_ID}
    assert _order_ref_candidate_ids({"tag": "INTERESADO"}) == set()


# --- wiring: el listado EMITE el campo -------------------------------------


class _FakeFactsPort:
    def __init__(self, snapshot: OrderFactsSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[set[str]] = []

    async def get_facts(self, order_ids):
        self.calls.append(set(order_ids))
        return self.snapshot


@pytest.fixture
def client_vault_port(tmp_path):
    app = FastAPI()
    app.include_router(dash_mod.router, prefix="/api/dashboard")
    port = _FakeFactsPort(_snap(_fact()))
    with patch.object(dash_mod, "WORKSPACE_VAULT_DIR", tmp_path), patch(
        "src.sdk.connectorkit.get_order_facts_port", return_value=port
    ):
        yield TestClient(app), tmp_path, port


def _seed(vault, session_id: str, metadata: dict) -> None:
    path = vault / session_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def test_sessions_list_emits_order_ref_from_one_facts_read(client_vault_port):
    """Gotcha #1: el listado debe EMITIRLO — y leer los pedidos en UN batch."""
    client, vault, port = client_vault_port
    _seed(vault, "wa_573001112233", _session_with_order())
    _seed(vault, "wa_573007654321", {"tag": "INTERESADO", "active_route": "ventas"})

    resp = client.get("/api/dashboard/sessions")

    assert resp.status_code == 200
    by_id = {s["session_id"]: s for s in resp.json()["sessions"]}
    assert by_id["wa_573001112233"]["order_ref"] == {
        "order_id": ORDER_ID,
        "display_id": "31",
        "payment": "pending",
        "count": 1,
    }
    assert by_id["wa_573007654321"]["order_ref"] is None
    assert port.calls == [{ORDER_ID}]


def test_sessions_list_hides_the_chip_of_a_draft(client_vault_port):
    client, vault, port = client_vault_port
    port.snapshot = _snap(_fact(is_draft=True))
    _seed(vault, "wa_573001112233", _session_with_order())

    resp = client.get("/api/dashboard/sessions")

    assert resp.json()["sessions"][0]["order_ref"] is None


def test_session_detail_emits_order_ref(client_vault_port):
    client, vault, _ = client_vault_port
    _seed(vault, "wa_573001112233", _session_with_order())

    resp = client.get("/api/dashboard/sessions/wa_573001112233")

    assert resp.json()["order_ref"]["display_id"] == "31"
