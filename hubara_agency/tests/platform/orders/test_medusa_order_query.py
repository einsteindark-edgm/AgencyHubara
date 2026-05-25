"""Tests del `MedusaOrderQuery` adapter — port read-side contra Medusa v2.

Usa **respx** (httpx mock) para interceptar las llamadas. Validamos:

  1. List devuelve summaries con shape correcto.
  2. Draft orders se intercalan con orders ordenados por created_at desc.
  3. status mapping (payment_status + fulfillment_status → UI status).
  4. pay_type mapping (metadata.payment_method).
  5. channel mapping (metadata.source).
  6. derived fields: short, color, due_iso, priority.
  7. failure path: Medusa 5xx → catalog_available=False + error_detail.
  8. detail: get() + fallback entre orders/draft-orders en 404.
  9. status mapping helpers son determinísticos.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.medusa_order_query import (
    MedusaOrderQuery,
    _color_from_id,
    _initials,
    _iso_to_ms,
    _map_pay_status,
    _map_status,
    _to_int_cop,
)

_BASE_URL = "http://medusa.test"


def _settings() -> MedusaSettings:
    return MedusaSettings(  # type: ignore[call-arg]
        base_url=_BASE_URL,
        admin_token="sk_test_abc",
    )


@pytest.fixture
async def adapter():
    settings = _settings()
    client = HttpMedusaClient(
        base_url=settings.base_url,
        admin_token=settings.admin_token,
        timeout=5.0,
    )
    ad = MedusaOrderQuery(client, settings)
    yield ad
    await client.aclose()


# ----------------------------------------------------------------------
# Helper sample payloads — minimal pero realistas
# ----------------------------------------------------------------------


def _sample_order(
    *,
    id: str = "order_01HX",
    display_id: int = 1247,
    total: float = 124500,
    status_payment: str = "captured",
    status_fulfillment: str = "not_fulfilled",
    payment_method: str = "transfer",
    source: str = "hubara_whatsapp_sales",
    customer_first: str = "María",
    customer_last: str = "Camila",
    city: str = "Bogotá",
    created_at: str = "2026-05-22T14:00:00.000Z",
) -> dict:
    return {
        "id": id,
        "display_id": display_id,
        "status": "pending",
        "payment_status": status_payment,
        "fulfillment_status": status_fulfillment,
        "email": "wa+wa_111@hubara.local",
        "currency_code": "cop",
        "created_at": created_at,
        "updated_at": created_at,
        "total": total,
        "subtotal": total - 5000,
        "shipping_total": 5000,
        "tax_total": 0,
        "discount_total": 0,
        "metadata": {
            "payment_method": payment_method,
            "source": source,
            "session_key": "wa_111",
        },
        "region_id": "reg_test_01",
        "customer_id": "cus_test_01",
        "sales_channel_id": "sc_test_01",
        "items": [
            {
                "title": "Vela Sagrado Corazón",
                "quantity": 2,
                "unit_price": 17000,
                "total": 34000,
                "sku": "VEL-001",
                "metadata": {"handle": "vela-sagrado-corazon"},
            },
            {
                "title": "Vela Inmaculada",
                "quantity": 1,
                "unit_price": 90500,
                "total": 90500,
                "sku": "VEL-003",
            },
        ],
        "shipping_address": {
            "first_name": customer_first,
            "last_name": customer_last,
            "phone": "3001234567",
            "address_1": "Calle 100 #15-20",
            "address_2": "Chapinero",
            "city": city,
            "country_code": "co",
        },
        "billing_address": None,
        "customer": {"first_name": customer_first, "last_name": customer_last},
        "transactions": [],
        "fulfillments": [],
    }


# ----------------------------------------------------------------------
# list()
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_returns_summaries_with_correct_shape(adapter):
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(
            200,
            json={
                "orders": [_sample_order()],
                "count": 1, "offset": 0, "limit": 50,
            },
        )
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200,
            json={"draft_orders": [], "count": 0, "offset": 0, "limit": 50},
        )
    )

    result = await adapter.list(limit=50, offset=0, include_drafts=True)
    assert result.catalog_available is True
    assert result.error_detail is None
    assert result.count == 1
    assert len(result.orders) == 1

    o = result.orders[0]
    assert o.id == "order_01HX"
    assert o.display_id == "#1247"
    assert o.customer == "María Camila"
    assert o.short == "MC"
    assert o.color in {"a", "b", "c", "d", "e", "f"}
    assert o.phone == "3001234567"
    assert o.city == "Bogotá"
    assert o.channel == "WhatsApp"
    assert o.status == "preparing"  # captured + not_fulfilled
    assert o.pay_status == "paid"
    assert o.pay_type == "confirmed"  # payment_method=transfer
    assert o.items == 2
    assert o.pieces == 3
    assert o.total_cop == 124500
    assert o.currency_code == "COP"
    assert o.is_draft is False
    # due estimate = created_at + 1 día
    assert o.due_iso == "2026-05-23"
    assert o.due_time == "—"
    assert o.overdue is False  # derivado client-side
    assert o.priority == "normal"  # 30k < 124500 < 200k
    assert o.agent == "—"
    assert o.created_at_ms > 0


@pytest.mark.asyncio
@respx.mock
async def test_list_merges_drafts_with_orders(adapter):
    # Draft order más reciente — debe salir primero.
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order(
                id="order_old", display_id=1240,
                created_at="2026-05-20T10:00:00.000Z",
            )],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={
            "draft_orders": [_sample_order(
                id="draft_new", display_id=1241,
                created_at="2026-05-22T14:00:00.000Z",
                status_fulfillment="not_fulfilled",
                status_payment="not_paid",
            )],
            "count": 1, "offset": 0, "limit": 50,
        })
    )

    result = await adapter.list(limit=50, include_drafts=True)
    assert len(result.orders) == 2
    # Más reciente primero.
    assert result.orders[0].id == "draft_new"
    assert result.orders[0].is_draft is True
    assert result.orders[0].status == "new"  # draft sin captura
    assert result.orders[1].id == "order_old"
    assert result.orders[1].is_draft is False


@pytest.mark.asyncio
@respx.mock
async def test_list_skips_drafts_when_disabled(adapter):
    orders_route = respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order()], "count": 1, "offset": 0, "limit": 50,
        })
    )
    drafts_route = respx.get(f"{_BASE_URL}/admin/draft-orders")

    result = await adapter.list(limit=50, include_drafts=False)
    assert orders_route.called
    assert not drafts_route.called
    assert len(result.orders) == 1


@pytest.mark.asyncio
@respx.mock
async def test_list_5xx_returns_empty_with_error_detail(adapter):
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(503, text="Service Unavailable")
    )
    # Si orders falla, no necesitamos mockear drafts (asyncio.gather levanta
    # con la primera excepción de cualquiera de los dos endpoints).

    result = await adapter.list(limit=50, include_drafts=True)
    assert result.catalog_available is False
    assert result.orders == []
    assert "503" in (result.error_detail or "")


@pytest.mark.asyncio
@respx.mock
async def test_list_pay_type_cod_when_metadata_says(adapter):
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order(payment_method="cash_on_delivery")],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0, "offset": 0, "limit": 50})
    )

    result = await adapter.list()
    assert result.orders[0].pay_type == "cod"


@pytest.mark.asyncio
@respx.mock
async def test_list_channel_defaults_to_web_when_not_whatsapp(adapter):
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order(source="shopify_import")],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0, "offset": 0, "limit": 50})
    )

    result = await adapter.list()
    assert result.orders[0].channel == "Web"


# ----------------------------------------------------------------------
# get() — detail endpoint
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_order_detail_full_shape(adapter):
    respx.get(f"{_BASE_URL}/admin/orders/order_01HX").mock(
        return_value=Response(200, json={"order": _sample_order()})
    )

    detail = await adapter.get("order_01HX")
    assert detail is not None
    assert detail.summary.id == "order_01HX"
    assert len(detail.items_detail) == 2
    assert detail.items_detail[0].title == "Vela Sagrado Corazón"
    assert detail.items_detail[0].quantity == 2
    assert detail.items_detail[0].unit_price_cop == 17000
    assert detail.items_detail[0].variant_label is None
    assert detail.items_detail[0].handle == "vela-sagrado-corazon"
    assert detail.shipping_address is not None
    assert detail.shipping_address.city == "Bogotá"
    assert detail.shipping_address.address_2 == "Chapinero"
    assert detail.subtotal_cop == 119500
    assert detail.shipping_cop == 5000
    assert detail.payment_method_label == "Transferencia"
    # Timeline minimo: solo 'created' (no transactions ni fulfillments)
    assert len(detail.timeline) == 1
    assert detail.timeline[0].type == "created"
    # data_completeness_missing debe listar los slots ausentes
    assert "due_date" in detail.data_completeness_missing
    assert "agent" in detail.data_completeness_missing
    assert "notes" in detail.data_completeness_missing
    assert "tracking_number" in detail.data_completeness_missing


@pytest.mark.asyncio
@respx.mock
async def test_get_draft_order_uses_draft_endpoint(adapter):
    draft_route = respx.get(f"{_BASE_URL}/admin/draft-orders/draft_xyz").mock(
        return_value=Response(200, json={"draft_order": _sample_order(id="draft_xyz")})
    )
    orders_route = respx.get(f"{_BASE_URL}/admin/orders/draft_xyz")

    detail = await adapter.get("draft_xyz")
    assert detail is not None
    assert detail.summary.is_draft is True
    assert draft_route.called
    assert not orders_route.called


@pytest.mark.asyncio
@respx.mock
async def test_get_falls_back_to_other_endpoint_on_404(adapter):
    """Si el prefix engaña (e.g. id 'order_foo' que en realidad es draft),
    el adapter falla a 404 en el primer endpoint y prueba el otro."""
    # Pide /admin/orders/X → 404
    respx.get(f"{_BASE_URL}/admin/orders/order_foo").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    # Fallback a /admin/draft-orders/X → existe
    respx.get(f"{_BASE_URL}/admin/draft-orders/order_foo").mock(
        return_value=Response(200, json={"draft_order": _sample_order(id="order_foo")})
    )
    detail = await adapter.get("order_foo")
    assert detail is not None
    assert detail.summary.is_draft is True  # vino del draft endpoint


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_none_when_404_both_endpoints(adapter):
    respx.get(f"{_BASE_URL}/admin/orders/order_missing").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders/order_missing").mock(
        return_value=Response(404, json={"message": "not found"})
    )
    detail = await adapter.get("order_missing")
    assert detail is None


# ----------------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------------


def test_map_status_cancelled_wins():
    assert _map_status(
        fulfillment_status="canceled", payment_status="not_paid", is_draft=False
    ) == "cancelled"
    # payment cancelled también marca cancelled
    assert _map_status(
        fulfillment_status="not_fulfilled", payment_status="canceled", is_draft=False
    ) == "cancelled"


def test_map_status_delivered_when_fulfilled_delivered():
    assert _map_status(
        fulfillment_status="delivered", payment_status="captured", is_draft=False
    ) == "delivered"
    assert _map_status(
        fulfillment_status="partially_delivered", payment_status="captured", is_draft=False
    ) == "delivered"


def test_map_status_shipping_when_shipped():
    assert _map_status(
        fulfillment_status="shipped", payment_status="captured", is_draft=False
    ) == "shipping"


def test_map_status_ready_when_fulfilled_but_not_shipped():
    assert _map_status(
        fulfillment_status="fulfilled", payment_status="captured", is_draft=False
    ) == "ready"


def test_map_status_draft_is_always_new_when_not_terminal():
    assert _map_status(
        fulfillment_status="not_fulfilled", payment_status="not_paid", is_draft=True
    ) == "new"


def test_map_status_preparing_when_payment_captured_but_not_fulfilled():
    assert _map_status(
        fulfillment_status="not_fulfilled", payment_status="captured", is_draft=False
    ) == "preparing"


def test_map_pay_status():
    assert _map_pay_status("captured") == "paid"
    assert _map_pay_status("authorized") == "paid"
    assert _map_pay_status("partially_captured") == "partial"
    assert _map_pay_status("refunded") == "refund"
    assert _map_pay_status("not_paid") == "pending"
    assert _map_pay_status(None) == "pending"


def test_initials_handles_edge_cases():
    assert _initials("María Camila") == "MC"
    assert _initials("Cliente") == "CL"
    assert _initials("") == "—"
    assert _initials("   ") == "—"
    assert _initials("Juan Pérez García") == "JP"


def test_color_from_id_deterministic():
    # Mismo id → mismo color, distinto id → puede ser distinto
    assert _color_from_id("order_01") == _color_from_id("order_01")
    assert _color_from_id("draft_xyz") in {"a", "b", "c", "d", "e", "f"}


def test_to_int_cop_handles_string_and_float():
    assert _to_int_cop(17000) == 17000
    assert _to_int_cop(17000.0) == 17000
    assert _to_int_cop("17000.5") == 17000  # round
    assert _to_int_cop(None) == 0
    assert _to_int_cop("abc") == 0  # tolera basura


def test_iso_to_ms_parses_z_suffix():
    ms = _iso_to_ms("2026-05-22T14:00:00.000Z")
    assert ms > 0
    # Verificamos que devuelve algo aproximado a la fecha esperada (epoch ms).
    # 2026-05-22T14:00:00 UTC ≈ 1779800400000
    assert 1779000000000 < ms < 1780000000000


def test_iso_to_ms_returns_0_on_invalid():
    assert _iso_to_ms(None) == 0
    assert _iso_to_ms("garbage") == 0
    assert _iso_to_ms("") == 0


# ----------------------------------------------------------------------
# Premortem J4 + A1 — regression tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_premortem_j4_401_returns_unauthorized_error_detail(adapter):
    """Premortem J4: cuando Medusa devuelve 401 (admin_token expirado), el
    error_detail debe ser explícito sobre el TOKEN — no 'medusa down'."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(401, json={"message": "Unauthorized"})
    )
    result = await adapter.list(limit=50, include_drafts=False)
    assert result.catalog_available is False
    assert result.error_detail is not None
    assert "medusa_unauthorized" in result.error_detail
    assert "admin_token" in result.error_detail.lower()


@pytest.mark.asyncio
@respx.mock
async def test_premortem_j4_403_returns_forbidden_error_detail(adapter):
    """Premortem J4: 403 = scopes insuficientes, mensaje específico."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(403, json={"message": "Forbidden"})
    )
    result = await adapter.list(limit=50, include_drafts=False)
    assert result.catalog_available is False
    assert "medusa_forbidden" in (result.error_detail or "")


@pytest.mark.asyncio
@respx.mock
async def test_premortem_j4_503_returns_unavailable_error_detail(adapter):
    """Premortem J4: 5xx → 'medusa_unavailable' (Medusa down). Distinto
    de 401."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(503, text="Service Unavailable")
    )
    result = await adapter.list(limit=50, include_drafts=False)
    assert result.catalog_available is False
    assert "medusa_unavailable" in (result.error_detail or "")


@pytest.mark.asyncio
@respx.mock
async def test_premortem_a1_get_accepts_display_id_with_hash(adapter):
    """Premortem A1: deep-link `/orders/#1247` debe funcionar. El backend
    resuelve "1247" → "order_01HX..." haciendo lookup en list_orders y
    matching `display_id`."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order(id="order_real_a1", display_id=1247)],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/orders/order_real_a1").mock(
        return_value=Response(200, json={"order": _sample_order(id="order_real_a1", display_id=1247)})
    )

    # Llamar con el display_id sin '#'.
    detail = await adapter.get("1247")
    assert detail is not None
    assert detail.summary.id == "order_real_a1"
    assert detail.summary.display_id == "#1247"


@pytest.mark.asyncio
@respx.mock
async def test_premortem_a1_get_accepts_display_id_with_hash_prefix(adapter):
    """Premortem A1: también con '#' adelante (lo que copia/pega el usuario
    desde la URL del kanban)."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={
            "orders": [_sample_order(id="order_real_a1b", display_id=1248)],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/orders/order_real_a1b").mock(
        return_value=Response(200, json={"order": _sample_order(id="order_real_a1b", display_id=1248)})
    )
    detail = await adapter.get("#1248")
    assert detail is not None
    assert detail.summary.id == "order_real_a1b"


@pytest.mark.asyncio
@respx.mock
async def test_premortem_a1_display_id_falls_back_to_draft_orders(adapter):
    """Premortem A1: si el display_id no está en /admin/orders, busca en
    /admin/draft-orders antes de devolver None."""
    # orders devuelve vacío
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={"orders": [], "count": 0, "offset": 0, "limit": 50})
    )
    # drafts devuelve uno con display_id=9999
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={
            "draft_orders": [_sample_order(id="draft_real_a1c", display_id=9999)],
            "count": 1, "offset": 0, "limit": 50,
        })
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders/draft_real_a1c").mock(
        return_value=Response(200, json={"draft_order": _sample_order(id="draft_real_a1c", display_id=9999)})
    )
    detail = await adapter.get("#9999")
    assert detail is not None
    assert detail.summary.id == "draft_real_a1c"
    assert detail.summary.is_draft is True


@pytest.mark.asyncio
@respx.mock
async def test_premortem_a1_display_id_returns_none_when_not_found(adapter):
    """Premortem A1: display_id no existe → None (NO error 500)."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={"orders": [], "count": 0, "offset": 0, "limit": 50})
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0, "offset": 0, "limit": 50})
    )
    detail = await adapter.get("#0000")
    assert detail is None
