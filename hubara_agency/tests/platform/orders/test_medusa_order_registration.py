"""Tests del `MedusaOrderRegistration` adapter — port live contra Medusa v2.

Usa **respx** (httpx mock) para interceptar las llamadas a
`HttpMedusaClient` sin tocar la red. Validamos:

  1. handle → variant_id resolution (con / sin variant_label).
  2. find-or-create customer (idempotency por email sintetizado).
  3. discover shipping_option_id (env override → cache, sino lookup).
  4. POST /admin/draft-orders con payload bien formado (campos required +
     metadata + shipping_methods).
  5. Success path: devuelve OrderRegistrationResult(success=True, order_id, ...).
  6. Failure paths:
     - Medusa 5xx → success=False con error_detail.
     - Product not found (handle inexistente) → success=False.
     - No shipping options configuradas → success=False.
  7. Config invalida (sin region_id) → constructor raise.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.medusa_order import (
    MedusaOrderConfigError,
    MedusaOrderRegistration,
)
from src.platform.orders.port import OrderItem, OrderShipping


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


_BASE_URL = "http://medusa.test"


def _settings(**overrides) -> MedusaSettings:
    defaults = dict(
        base_url=_BASE_URL,
        admin_token="sk_test_abc",
        region_id="reg_test_01",
        sales_channel_id="sc_test_01",
        default_currency="cop",
        default_country="co",
        default_shipping_option_id=None,
    )
    defaults.update(overrides)
    return MedusaSettings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def adapter():
    """Build a real adapter wired through a real HttpMedusaClient.

    respx fixture intercepts httpx calls at the transport layer. We do NOT
    mock the client itself — that way the full request/response cycle
    (auth headers, retries, JSON parsing) is exercised.
    """
    settings = _settings()
    client = HttpMedusaClient(
        base_url=settings.base_url,
        admin_token=settings.admin_token,
        timeout=5.0,
    )
    service = MedusaProductService(client)
    ad = MedusaOrderRegistration(client, service, settings)
    yield ad
    await client.aclose()


_ITEMS = [
    OrderItem(
        handle="cruz-de-vida",
        quantity=1,
        unit_price_cop=17000,
        variant_label="Lavanda",
    )
]

_SHIPPING = OrderShipping(
    city="Bogotá",
    neighborhood="Chapinero",
    address="Calle 100 #15-20 Apto 502",
    phone="3001234567",
)


def _product_payload_for_handle(handle: str) -> dict:
    """Build a Medusa list_products response body with one product + 2 variants."""
    return {
        "products": [
            {
                "id": "prod_01",
                "title": "Cruz de Vida",
                "handle": handle,
                "status": "published",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "variants": [
                    {
                        "id": "var_default",
                        "title": "Default",
                        "sku": "CDV-DEF",
                        "manage_inventory": False,
                        "allow_backorder": False,
                        "prices": [],
                        "options": [{"id": "ov1", "value": "Default"}],
                    },
                    {
                        "id": "var_lavanda",
                        "title": "Lavanda",
                        "sku": "CDV-LAV",
                        "manage_inventory": False,
                        "allow_backorder": False,
                        "prices": [],
                        "options": [{"id": "ov2", "value": "Lavanda"}],
                    },
                ],
                "options": [],
                "images": [],
                "tags": [],
                "categories": [],
                "sales_channels": [],
            }
        ],
        "count": 1,
        "offset": 0,
        "limit": 1,
    }


# ----------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------


def test_adapter_rejects_missing_region_id():
    """Sin MEDUSA_REGION_ID el adapter no puede construirse — composition
    debe caer al stub explicitamente."""
    settings = _settings(region_id=None)
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test")
    service = MedusaProductService(client)
    with pytest.raises(MedusaOrderConfigError, match="MEDUSA_REGION_ID"):
        MedusaOrderRegistration(client, service, settings)


def test_adapter_rejects_missing_sales_channel_id():
    settings = _settings(sales_channel_id=None)
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test")
    service = MedusaProductService(client)
    with pytest.raises(MedusaOrderConfigError, match="MEDUSA_SALES_CHANNEL_ID"):
        MedusaOrderRegistration(client, service, settings)


# ----------------------------------------------------------------------
# Success path — full happy flow
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_register_order_success_full_flow(adapter):
    # 1) handle → variant lookup
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    # 2) customer lookup → empty → create
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(
            200,
            json={"customer": {"id": "cus_new_001", "email": "wa+wa_111@hubara.local"}},
        )
    )
    # 3) shipping options discovery
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(
            200,
            json={
                "shipping_options": [
                    {"id": "so_std_01", "name": "Envío estándar"},
                ],
                "count": 1,
                "offset": 0,
                "limit": 50,
            },
        )
    )
    # 4) POST draft-orders
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200,
            json={
                "draft_order": {
                    "id": "draft_xyz_001",
                    "status": "draft",
                    "items": [],
                    "shipping_methods": [],
                }
            },
        )
    )

    result = await adapter.register_order(
        session_key="wa_111",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=5000,
        total_cop=22000,
    )

    assert result.success is True
    assert result.order_id == "draft_xyz_001"
    assert result.provider == "medusa"
    assert result.customer_id == "cus_new_001"
    # items_resolved tiene el variant elegido por label (lavanda)
    assert result.items_resolved[0]["variant_id"] == "var_lavanda"
    assert result.items_resolved[0]["quantity"] == 1
    assert result.items_resolved[0]["unit_price"] == 17000

    # Verificar payload del draft-order: campos required de OpenAPI + metadata.
    posted = draft_route.calls[0].request
    import json as _json
    body = _json.loads(posted.content)
    assert body["sales_channel_id"] == "sc_test_01"
    assert body["region_id"] == "reg_test_01"
    assert body["currency_code"] == "cop"
    assert body["email"] == "wa+wa_111@hubara.local"
    assert body["customer_id"] == "cus_new_001"
    assert body["shipping_address"]["city"] == "Bogotá"
    assert body["shipping_address"]["address_1"] == "Calle 100 #15-20 Apto 502"
    assert body["shipping_address"]["address_2"] == "Chapinero"
    assert body["shipping_address"]["country_code"] == "co"
    assert body["shipping_address"]["phone"] == "3001234567"
    assert len(body["shipping_methods"]) == 1
    # Medusa v2 admin schema: campo correcto es `shipping_option_id`
    # (no `option_id`). Bug detectado en run bc54cb93 (HTTP 400).
    assert body["shipping_methods"][0]["shipping_option_id"] == "so_std_01"
    assert body["shipping_methods"][0]["amount"] == 5000
    assert body["metadata"]["session_key"] == "wa_111"
    assert body["metadata"]["payment_method"] == "transfer"
    assert body["metadata"]["total_cop"] == 22000
    assert body["no_notification_order"] is True


@pytest.mark.asyncio
@respx.mock
async def test_register_order_reuses_existing_customer(adapter):
    """Si Medusa ya tiene un customer con el email sintetizado, el adapter lo
    reusa en vez de crear duplicado (idempotency en retries)."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    # customer lookup devuelve uno existente
    customer_get = respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(
            200,
            json={
                "customers": [
                    {"id": "cus_existing_001", "email": "wa+wa_222@hubara.local"}
                ],
                "count": 1,
                "offset": 0,
                "limit": 1,
            },
        )
    )
    customer_create = respx.post(f"{_BASE_URL}/admin/customers")
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(
            200,
            json={"shipping_options": [{"id": "so_01", "name": "Std"}], "count": 1, "offset": 0, "limit": 50},
        )
    )
    respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200, json={"draft_order": {"id": "draft_reused", "status": "draft"}}
        )
    )

    result = await adapter.register_order(
        session_key="wa_222",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="card",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    assert result.success is True
    assert result.customer_id == "cus_existing_001"
    # Verifica que NO se creó un nuevo customer.
    assert customer_get.called
    assert not customer_create.called


@pytest.mark.asyncio
@respx.mock
async def test_register_order_uses_env_shipping_option_when_set():
    """Si MEDUSA_DEFAULT_SHIPPING_OPTION_ID está seteado, el adapter NO
    consulta /admin/shipping-options (zero round-trips)."""
    settings = _settings(default_shipping_option_id="so_env_force_01")
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test_abc", timeout=5.0)
    try:
        service = MedusaProductService(client)
        ad = MedusaOrderRegistration(client, service, settings)

        respx.get(f"{_BASE_URL}/admin/products").mock(
            return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
        )
        respx.get(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
        )
        respx.post(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(
                200, json={"customer": {"id": "cus_new", "email": "wa+wa_333@hubara.local"}}
            )
        )
        shipping_options_route = respx.get(f"{_BASE_URL}/admin/shipping-options")
        draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
            return_value=Response(
                200, json={"draft_order": {"id": "draft_env", "status": "draft"}}
            )
        )

        result = await ad.register_order(
            session_key="wa_333",
            items=_ITEMS,
            shipping=_SHIPPING,
            payment_method="cash_on_delivery",
            subtotal_cop=17000,
            shipping_cop=8000,
            total_cop=25000,
        )
        assert result.success is True
        # No se consultó shipping options (env override).
        assert not shipping_options_route.called
        # El draft order usó la option_id del env.
        import json as _json
        body = _json.loads(draft_route.calls[0].request.content)
        assert body["shipping_methods"][0]["shipping_option_id"] == "so_env_force_01"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_register_order_picks_first_variant_when_no_label(adapter):
    """Sin variant_label, el adapter cae a la primera variante (sin matchear)."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_anon", "email": "wa+wa_no_label@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Std"}], "count": 1, "offset": 0, "limit": 50})
    )
    respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_no_label", "status": "draft"}})
    )

    items_no_label = [OrderItem(handle="cruz-de-vida", quantity=2, unit_price_cop=17000)]
    result = await adapter.register_order(
        session_key="wa_no_label",
        items=items_no_label,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=34000,
        shipping_cop=0,
        total_cop=34000,
    )
    assert result.success is True
    # Sin label → primera variante (var_default).
    assert result.items_resolved[0]["variant_id"] == "var_default"


# ----------------------------------------------------------------------
# Failure paths
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_register_order_5xx_returns_failure_with_detail(adapter):
    """Si Medusa devuelve 5xx en draft-orders, el adapter NO levanta — devuelve
    OrderRegistrationResult(success=False) con error_detail para el LLM."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_x", "email": "wa+wa_x@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Std"}], "count": 1, "offset": 0, "limit": 50})
    )
    respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(503, text="Service Unavailable")
    )

    result = await adapter.register_order(
        session_key="wa_x",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    assert result.success is False
    assert result.order_id is None
    assert "503" in (result.error_detail or "")


@pytest.mark.asyncio
@respx.mock
async def test_register_order_product_not_found_returns_failure(adapter):
    """Si el handle no existe en Medusa, el adapter falla limpio (NO crea
    customer ni draft order)."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json={"products": [], "count": 0, "offset": 0, "limit": 1})
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders")

    result = await adapter.register_order(
        session_key="wa_missing",
        items=[OrderItem(handle="producto-inexistente", quantity=1, unit_price_cop=10000)],
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=10000,
        shipping_cop=0,
        total_cop=10000,
    )
    assert result.success is False
    assert "not found" in (result.error_detail or "").lower()
    # Critical: no se intentó crear el draft order.
    assert not draft_route.called


@pytest.mark.asyncio
@respx.mock
async def test_register_order_no_shipping_options_returns_failure(adapter):
    """Si el operador no creó shipping options, el adapter falla limpio."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_y", "email": "wa+y@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [], "count": 0, "offset": 0, "limit": 50})
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders")

    result = await adapter.register_order(
        session_key="wa_no_ship",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    assert result.success is False
    assert "shipping" in (result.error_detail or "").lower()
    assert not draft_route.called


# ----------------------------------------------------------------------
# Premortem fixes — regression tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_premortem_h1_prefers_shipping_with_envio_keyword():
    """Premortem H1: cuando hay multiples shipping options, el adapter
    elige la que tiene 'envio' / 'shipping' / 'domicilio' en el nombre —
    NO la primera por orden de creación (que típicamente es 'Recogida')."""
    settings = _settings()
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test_abc", timeout=5.0)
    try:
        service = MedusaProductService(client)
        ad = MedusaOrderRegistration(client, service, settings)

        respx.get(f"{_BASE_URL}/admin/products").mock(
            return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
        )
        respx.get(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
        )
        respx.post(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(200, json={"customer": {"id": "cus_x", "email": "wa+x@hubara.local"}})
        )
        # 3 options — "Recogida en tienda" primero (created_at asc),
        # luego "Envío estándar", luego "Express". Sin smart pick, el
        # adapter elegiría "Recogida". CON el fix, elige "Envío estándar".
        respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
            return_value=Response(200, json={
                "shipping_options": [
                    {"id": "so_pickup_01", "name": "Recogida en tienda"},
                    {"id": "so_standard_01", "name": "Envío estándar"},
                    {"id": "so_express_01", "name": "Express"},
                ],
                "count": 3, "offset": 0, "limit": 50,
            })
        )
        draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
            return_value=Response(200, json={"draft_order": {"id": "draft_h1"}})
        )

        result = await ad.register_order(
            session_key="wa_h1",
            items=_ITEMS,
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=5000,
            total_cop=22000,
        )
        assert result.success is True
        import json as _json
        body = _json.loads(draft_route.calls[0].request.content)
        # CRITICAL: debe ser el de envío, NO el de recogida.
        assert body["shipping_methods"][0]["shipping_option_id"] == "so_standard_01"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_premortem_b1_idempotency_key_in_metadata(adapter):
    """Premortem B1: el draft order POST incluye `metadata.idempotency_key`
    para que el backend pueda deduplicar si el LLM o el workflow retrying
    llaman 2x dentro de la ventana de 10min."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_b1", "email": "wa+b1@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Envío"}], "count": 1, "offset": 0, "limit": 50})
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_b1"}})
    )

    await adapter.register_order(
        session_key="wa_idempotent",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    import json as _json
    body = _json.loads(draft_route.calls[0].request.content)
    assert "idempotency_key" in body["metadata"]
    # El key incluye el session_key + un bucket numérico.
    assert body["metadata"]["idempotency_key"].startswith("wa_idempotent:")


@pytest.mark.asyncio
@respx.mock
async def test_premortem_b1_same_session_in_same_bucket_gets_same_key():
    """Premortem B1: dos llamadas del mismo session_key dentro de 10min
    producen el mismo idempotency_key — habilitando dedup."""
    settings = _settings()
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test_abc", timeout=5.0)
    try:
        service = MedusaProductService(client)
        ad = MedusaOrderRegistration(client, service, settings)

        respx.get(f"{_BASE_URL}/admin/products").mock(
            return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
        )
        respx.get(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
        )
        respx.post(f"{_BASE_URL}/admin/customers").mock(
            return_value=Response(200, json={"customer": {"id": "cus_b1b", "email": "wa+b1b@hubara.local"}})
        )
        respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
            return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Envío"}], "count": 1, "offset": 0, "limit": 50})
        )
        draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
            return_value=Response(200, json={"draft_order": {"id": "draft_x"}})
        )

        common_kwargs = dict(
            session_key="wa_dup",
            items=_ITEMS,
            shipping=_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
        await ad.register_order(**common_kwargs)
        await ad.register_order(**common_kwargs)

        import json as _json
        key_a = _json.loads(draft_route.calls[0].request.content)["metadata"]["idempotency_key"]
        key_b = _json.loads(draft_route.calls[1].request.content)["metadata"]["idempotency_key"]
        # Dos llamadas inmediatas (< 10min) → mismo bucket → mismo key.
        assert key_a == key_b
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_premortem_h3_variant_mismatch_surfaced_in_metadata(adapter):
    """Premortem H3: cuando el variant_label no matchea ninguna variante
    en Medusa, el draft order incluye `metadata.variant_mismatches` con
    el detalle del fallback — visible al operador."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_h3", "email": "wa+h3@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Envío"}], "count": 1, "offset": 0, "limit": 50})
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_h3"}})
    )

    # Item con label que NO existe en las 2 variantes del fixture (Default, Lavanda).
    items_bad_label = [OrderItem(
        handle="cruz-de-vida", quantity=1, unit_price_cop=17000,
        variant_label="Color Inventado Xyz",
    )]
    await adapter.register_order(
        session_key="wa_h3",
        items=items_bad_label,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    import json as _json
    body = _json.loads(draft_route.calls[0].request.content)
    assert "variant_mismatches" in body["metadata"]
    assert len(body["metadata"]["variant_mismatches"]) == 1
    mm = body["metadata"]["variant_mismatches"][0]
    assert mm["requested_label"] == "Color Inventado Xyz"
    assert mm["handle"] == "cruz-de-vida"
    # Y el item resolved tiene el flag inline también.
    assert body["items"][0]["metadata"]["variant_label_mismatch"] is True


@pytest.mark.asyncio
@respx.mock
async def test_premortem_h3_no_mismatch_when_label_matches(adapter):
    """Premortem H3 (regression): cuando el label SI matchea, NO se agrega
    `variant_mismatches` (no spam de metadata)."""
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_h3b", "email": "wa+h3b@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_01", "name": "Envío"}], "count": 1, "offset": 0, "limit": 50})
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_h3b"}})
    )

    # _ITEMS usa variant_label="Lavanda" que SI existe en el fixture.
    await adapter.register_order(
        session_key="wa_h3_good",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    import json as _json
    body = _json.loads(draft_route.calls[0].request.content)
    assert "variant_mismatches" not in body["metadata"]


@pytest.mark.asyncio
async def test_premortem_c1_timeout_returns_failure_with_detail(adapter, monkeypatch):
    """Premortem C1: si el adapter excede _REGISTER_ORDER_TIMEOUT_S total,
    devuelve success=False con error_detail='timeout: ...' — NO se cuelga
    el activity execute_tool indefinidamente."""
    import src.platform.orders.medusa_order as mo

    # Reduce el timeout para no hacer al test lento.
    monkeypatch.setattr(mo, "_REGISTER_ORDER_TIMEOUT_S", 0.5)

    # Simulate hang: el inner method nunca termina.
    async def hung_inner(**_):
        import asyncio
        await asyncio.sleep(10)
        return None  # nunca llega

    monkeypatch.setattr(adapter, "_register_order_inner", hung_inner)

    result = await adapter.register_order(
        session_key="wa_timeout",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    assert result.success is False
    assert "timeout" in (result.error_detail or "").lower()


def test_premortem_h2_warn_when_currency_not_cop(caplog):
    """Premortem H2: el constructor del adapter loguea warning si
    default_currency != 'cop' — defensa contra config mal copiada de USD."""
    import logging
    import src.platform.orders.medusa_order as mo

    settings = _settings()
    # mutamos manualmente (Pydantic v2 settings son inmutable por default —
    # pero el __init__ del adapter solo lee el field, asi que lo monkey-patcheamos).
    object.__setattr__(settings, "default_currency", "usd")

    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    service = MedusaProductService(client)
    with caplog.at_level(logging.WARNING, logger=mo.__name__):
        mo.MedusaOrderRegistration(client, service, settings)
    # Buscar el warning sobre currency.
    assert any(
        "default_currency" in rec.message.lower() and "cop" in rec.message.lower()
        for rec in caplog.records
    )


# =============================================================================
# Compound variant_label parsing — post-mortem run bc54cb93 (2026-05-25)
# El LLM pasa labels combinados cuando un producto tiene 2 variant options
# (aroma + color). El adapter debe splittearlos y buscar la variante con
# mejor coverage en lugar de hacer fallback ciego a la primera.
# =============================================================================


def test_split_variant_label_single_token():
    """Un label sin separadores se devuelve como lista de 1 elemento."""
    from src.platform.orders.medusa_order import _split_variant_label

    assert _split_variant_label("Lavanda") == ["Lavanda"]
    assert _split_variant_label("  Lavanda  ") == ["Lavanda"]
    assert _split_variant_label("") == []
    assert _split_variant_label("   ") == []


def test_split_variant_label_comma_separator():
    """`"Frutos rojos, Marrón"` → ["Frutos rojos", "Marrón"] (caso real
    detectado en run bc54cb93)."""
    from src.platform.orders.medusa_order import _split_variant_label

    assert _split_variant_label("Frutos rojos, Marrón") == [
        "Frutos rojos", "Marrón",
    ]
    # Comma sin espacio igual funciona
    assert _split_variant_label("Lavanda,Blanco") == ["Lavanda", "Blanco"]


def test_split_variant_label_slash_and_dash_separators():
    """Otros separadores comunes que el LLM puede usar."""
    from src.platform.orders.medusa_order import _split_variant_label

    assert _split_variant_label("Lavanda / Blanco") == ["Lavanda", "Blanco"]
    assert _split_variant_label("Lavanda - Blanco") == ["Lavanda", "Blanco"]
    assert _split_variant_label("Lavanda | Blanco") == ["Lavanda", "Blanco"]
    # Slash sin espacios igual splittea
    assert _split_variant_label("Lavanda/Blanco") == ["Lavanda", "Blanco"]


def test_count_matching_parts_full_coverage():
    """Una variante cuyo title contiene todos los tokens da score == len(parts)."""
    from src.platform.orders.medusa_order import _count_matching_parts

    variant = type("V", (), {
        "title": "Frutos rojos / Marrón",
        "options": [],
    })()
    assert _count_matching_parts(variant, ["Frutos rojos", "Marrón"]) == 2


def test_count_matching_parts_partial_coverage():
    """Si solo un token está, score es 1 (no full match pero non-zero)."""
    from src.platform.orders.medusa_order import _count_matching_parts

    variant = type("V", (), {
        "title": "Lavanda / Blanco",
        "options": [],
    })()
    assert _count_matching_parts(variant, ["Lavanda", "Marrón"]) == 1
    assert _count_matching_parts(variant, ["Frutos rojos", "Marrón"]) == 0


def test_count_matching_parts_uses_option_values():
    """Si la variante tiene opt.value en vez de title, también matchea."""
    from src.platform.orders.medusa_order import _count_matching_parts

    variant = type("V", (), {
        "title": "Variant 1",  # title no informativo
        "options": [
            type("O", (), {"value": "Lavanda"})(),
            type("O", (), {"value": "Blanco"})(),
        ],
    })()
    assert _count_matching_parts(variant, ["Lavanda", "Blanco"]) == 2


def test_pick_variant_compound_label_picks_best_coverage():
    """Integration: `"Frutos rojos, Marrón"` debe matchear la variante con
    AMBOS tokens, no caer a fallback. Es el bug original del run bc54cb93."""

    # Stubs locales del shape ProductVariant que el método lee (title + options).
    variants = [
        # variant 0: no matchea ninguno → fallback ciego ANTES iba acá.
        type("V", (), {
            "title": "Lavanda / Blanco",
            "options": [
                type("O", (), {"value": "Lavanda"})(),
                type("O", (), {"value": "Blanco"})(),
            ],
        })(),
        # variant 1: matchea ambos tokens → DEBE ganar.
        type("V", (), {
            "title": "Frutos rojos / Marrón",
            "options": [
                type("O", (), {"value": "Frutos rojos"})(),
                type("O", (), {"value": "Marrón"})(),
            ],
        })(),
    ]
    product = type("P", (), {
        "handle": "cruz-de-vida",
        "variants": variants,
    })()

    # Instanciamos un adapter mínimo solo para llamar el método (no toca red).
    settings = _settings()
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    service = MedusaProductService(client)
    adapter = MedusaOrderRegistration(client, service, settings)

    chosen, had_mismatch = adapter._pick_variant_with_status(
        product, "Frutos rojos, Marrón",
    )
    assert chosen is variants[1], (
        "Debe elegir la variante con coverage=2, no la primera"
    )
    assert had_mismatch is False, (
        "Coverage completo (2/2) → no es mismatch"
    )


def test_pick_variant_partial_coverage_still_picks_best_but_surfaces_mismatch():
    """Si solo un token matchea (coverage parcial), elige esa variante pero
    marca `had_mismatch=True` para que se surface en metadata."""
    variants = [
        type("V", (), {
            "title": "Lavanda / Blanco",
            "options": [
                type("O", (), {"value": "Lavanda"})(),
                type("O", (), {"value": "Blanco"})(),
            ],
        })(),
        type("V", (), {
            "title": "Sándalo / Negro",
            "options": [
                type("O", (), {"value": "Sándalo"})(),
                type("O", (), {"value": "Negro"})(),
            ],
        })(),
    ]
    product = type("P", (), {
        "handle": "test-product",
        "variants": variants,
    })()
    settings = _settings()
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    service = MedusaProductService(client)
    adapter = MedusaOrderRegistration(client, service, settings)

    # "Lavanda, Marrón" → variant 0 matchea "Lavanda" (1 token), variant 1 matchea 0.
    chosen, had_mismatch = adapter._pick_variant_with_status(
        product, "Lavanda, Marrón",
    )
    assert chosen is variants[0]
    assert had_mismatch is True  # 1/2 — parcial → surface


# Nota: los stubs custom (`type("V", ...)`) no implementan el protocol completo
# de ProductVariant pero alcanzan para los 2 atributos que `_count_matching_parts`
# lee: `title` y `options[].value`. Eso preserva la lógica de coverage sin
# acoplarse al shape Pydantic real (que cambia entre versiones de Medusa).


# ----------------------------------------------------------------------
# Idempotencia (fix integridad orden↔tag): fingerprint + pre-check
# ----------------------------------------------------------------------


def test_fingerprint_is_stable_and_content_sensitive():
    """Mismo contenido → mismo fingerprint; distinto contenido → distinto."""
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    fp1 = _compute_order_fingerprint(_ITEMS, 22000, "transfer")
    assert fp1 == _compute_order_fingerprint(_ITEMS, 22000, "transfer")  # determinístico
    # Distinto total / método → distinto (no deduplica compras legítimas distintas).
    assert _compute_order_fingerprint(_ITEMS, 25000, "transfer") != fp1
    assert _compute_order_fingerprint(_ITEMS, 22000, "card") != fp1
    # El orden de los items no cambia el hash (sorted internamente).
    two = [
        _ITEMS[0],
        OrderItem(handle="vela-otra", quantity=2, unit_price_cop=5000),
    ]
    assert _compute_order_fingerprint(two, 1, "x") == _compute_order_fingerprint(
        list(reversed(two)), 1, "x"
    )


@pytest.mark.asyncio
@respx.mock
async def test_idempotency_pre_check_reuses_existing_draft(adapter):
    """Si ya existe un draft con el mismo (session_key, fingerprint), el adapter
    lo reusa SIN crear uno nuevo — cubre el retry de Temporal tras un crash que
    ocurrió DESPUÉS de crear la orden en Medusa pero antes de reportar a Temporal."""
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    fp = _compute_order_fingerprint(_ITEMS, 22000, "transfer")
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200,
            json={
                "draft_orders": [
                    {
                        "id": "draft_already_there",
                        "customer_id": "cus_prev",
                        "metadata": {
                            "session_key": "wa_111",
                            "order_fingerprint": fp,
                        },
                    }
                ],
                "count": 1, "offset": 0, "limit": 50,
            },
        )
    )
    # Estas rutas NO deben llamarse (return temprano por idempotencia).
    products_route = respx.get(f"{_BASE_URL}/admin/products")
    create_route = respx.post(f"{_BASE_URL}/admin/draft-orders")

    result = await adapter.register_order(
        session_key="wa_111",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=5000,
        total_cop=22000,
    )

    assert result.success is True
    assert result.order_id == "draft_already_there"  # reusado, NO uno nuevo
    assert not products_route.called  # ni siquiera resolvió items
    assert not create_route.called    # NO creó draft duplicado


@pytest.mark.asyncio
@respx.mock
async def test_idempotency_distinct_content_creates_new_draft(adapter):
    """Un draft reciente con fingerprint DISTINTO no bloquea: el adapter crea el
    nuevo (no es falso-positivo entre compras distintas) y embebe el
    `order_fingerprint` en el metadata del draft nuevo para futuros pre-checks."""
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    fp_other = _compute_order_fingerprint(
        [OrderItem(handle="otra-cosa", quantity=9, unit_price_cop=999)], 9, "card"
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200,
            json={
                "draft_orders": [
                    {
                        "id": "draft_old",
                        "metadata": {
                            "session_key": "wa_111",
                            "order_fingerprint": fp_other,
                        },
                    }
                ],
                "count": 1, "offset": 0, "limit": 50,
            },
        )
    )
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(
            200, json={"customer": {"id": "cus_new", "email": "wa+wa_111@hubara.local"}}
        )
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(
            200,
            json={"shipping_options": [{"id": "so_01", "name": "Envío estándar"}], "count": 1, "offset": 0, "limit": 50},
        )
    )
    draft_route = respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_new_001", "status": "draft"}})
    )

    result = await adapter.register_order(
        session_key="wa_111",
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=5000,
        total_cop=22000,
    )

    assert result.success is True
    assert result.order_id == "draft_new_001"  # creó uno nuevo
    assert draft_route.called
    import json as _json
    body = _json.loads(draft_route.calls[0].request.content)
    assert body["metadata"]["order_fingerprint"] == _compute_order_fingerprint(
        _ITEMS, 22000, "transfer"
    )
    assert body["metadata"]["idempotency_key"].startswith("wa_111:")
