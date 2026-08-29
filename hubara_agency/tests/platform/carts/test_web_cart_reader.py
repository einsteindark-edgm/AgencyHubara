"""Contract + adapter tests del WebCartReaderPort (fase roja — TDD).

Port nuevo `src/platform/carts/` (espejo de `src/platform/orders/`): lee un
carrito de la Store API de Medusa v2 (`GET /store/carts/{id}` con header
`x-publishable-api-key`).

Regla de oro del ConnectorKit (docs/_sdk/07-connectorkit.md): port nuevo ⇒
Protocol + factory + fake + contract suite parametrizada fake/real. Los tests
de comportamiento del port corren contra AMBOS lados (patrón
`tests/platform/test_attribution_store.py`); los de parse/HTTP solo contra el
adapter (patrón respx de `tests/platform/medusa/test_client_retries.py`).

Semántica clave bajo test:
  * 200 → parse TOLERANTE de `{"cart": {...}}` (items malformados se
    descartan, cart sin items es válido con items=()).
  * no-2xx (404/500) → None, sin excepción.
  * errores de transporte httpx → PROPAGAN (regla 4 del ConnectorKit: el
    caller degrada, el adapter no miente con None).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.carts.composition import get_web_cart_reader
from src.platform.carts.medusa_store import MedusaStoreCartReader
from src.platform.carts.port import (
    FakeWebCartReader,
    NullWebCartReader,
    WebCartItem,
    WebCartSnapshot,
)

BASE_URL = "https://m.test"
PUB_KEY = "pk_test_123"


def _reader() -> MedusaStoreCartReader:
    return MedusaStoreCartReader(base_url=BASE_URL, publishable_api_key=PUB_KEY)


# ---------------------------------------------------------------------------
# El lado "fake" de la contract suite = FakeWebCartReader, el fake OFICIAL
# promovido junto al port (regla 2/3 del kit) — mismo objeto que usan los
# tests de plugins vía src.sdk.connectorkit.
# ---------------------------------------------------------------------------


def _wire_cart(snap: WebCartSnapshot) -> dict:
    """Serializa un snapshot al shape Store API v2 (inverso del mapping)."""
    body: dict = {
        "id": snap.cart_id,
        "items": [
            {
                "product_title": it.product_title,
                "quantity": it.quantity,
                "product_handle": it.product_handle,
                "variant_title": it.variant_title,
                "unit_price": it.unit_price,
            }
            for it in snap.items
        ],
    }
    if snap.email is not None:
        body["email"] = snap.email
    if snap.currency_code is not None:
        body["currency_code"] = snap.currency_code
    shipping: dict = {}
    if snap.city is not None:
        shipping["city"] = snap.city
    if snap.phone is not None:
        shipping["phone"] = snap.phone
    if snap.address is not None:
        shipping["address_1"] = snap.address  # snapshots del contrato: sin address_2
    if snap.customer_name is not None:
        first, _, last = snap.customer_name.partition(" ")
        shipping["first_name"] = first
        if last:
            shipping["last_name"] = last
    if shipping:
        body["shipping_address"] = shipping
    return {"cart": body}


# ---------------------------------------------------------------------------
# Contract suite — misma semántica observada en fake y adapter real.
# ---------------------------------------------------------------------------

_CONTRACT_SNAPSHOT = WebCartSnapshot(
    cart_id="cart_01CONTRACT",
    items=(
        WebCartItem(
            product_title="Vela Ángel",
            quantity=2,
            product_handle="vela-angel",
            variant_title="Grande",
            unit_price=45000.0,
        ),
    ),
    email="cliente@test.com",
    phone="573001234567",
    city="Bogotá",
    address="Cra 7 # 12-34",
    customer_name="Ana Pardo",
    currency_code="cop",
)

_EMPTY_SNAPSHOT = WebCartSnapshot(cart_id="cart_01EMPTY", items=())


@pytest.fixture(params=["medusa", "fake"])
def make_reader(request):
    """Harness de la contract suite: `make(snapshots) -> WebCartReaderPort`.

    - "fake": FakeWebCartReader directo sobre los snapshots.
    - "medusa": serializa cada snapshot al wire format y lo monta en respx;
      cualquier otro cart_id responde 404 (→ None, por contrato).
    """
    if request.param == "fake":
        yield FakeWebCartReader
        return
    # assert_all_called=False: el catch-all 404 solo se usa en el test del
    # cart desconocido — que quede sin llamar en los demás no es un error.
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        def _make(snapshots: dict[str, WebCartSnapshot]):
            for cart_id, snap in snapshots.items():
                router.get(f"/store/carts/{cart_id}").mock(
                    return_value=httpx.Response(200, json=_wire_cart(snap))
                )
            router.get(url__regex=r"/store/carts/.*").mock(
                return_value=httpx.Response(404, json={"type": "not_found"})
            )
            return _reader()
        yield _make


@pytest.mark.asyncio
async def test_contract_known_cart_returns_mapped_snapshot(make_reader):
    reader = make_reader({_CONTRACT_SNAPSHOT.cart_id: _CONTRACT_SNAPSHOT})
    snap = await reader.get_cart("cart_01CONTRACT")
    assert snap == _CONTRACT_SNAPSHOT


@pytest.mark.asyncio
async def test_contract_unknown_cart_returns_none(make_reader):
    reader = make_reader({_CONTRACT_SNAPSHOT.cart_id: _CONTRACT_SNAPSHOT})
    assert await reader.get_cart("cart_NO_EXISTE") is None


@pytest.mark.asyncio
async def test_contract_cart_without_items_is_valid_empty_snapshot(make_reader):
    reader = make_reader({_EMPTY_SNAPSHOT.cart_id: _EMPTY_SNAPSHOT})
    snap = await reader.get_cart("cart_01EMPTY")
    assert snap is not None
    assert snap.items == ()


@pytest.mark.asyncio
async def test_null_reader_always_returns_none():
    """NullWebCartReader = fallback de composición sin config: siempre None."""
    assert await NullWebCartReader().get_cart("cart_cualquiera") is None


# ---------------------------------------------------------------------------
# Adapter-only: parse tolerante + semántica HTTP (respx).
# ---------------------------------------------------------------------------

_FULL_PAYLOAD = {
    "cart": {
        "id": "cart_01ABC",
        "email": "cliente@test.com",
        "currency_code": "cop",
        "items": [
            {
                "id": "cali_01",
                "title": "Grande",
                "product_title": "Vela Ángel",
                "product_handle": "vela-angel",
                "variant_title": "Grande",
                "quantity": 2,
                "unit_price": 45000,
            },
            # item mínimo: solo lo requerido — opcionales quedan None
            {"product_title": "Velón Zodiacal", "quantity": 1},
        ],
        "shipping_address": {
            "first_name": "Ana",
            "last_name": "Pardo",
            "address_1": "Cra 7 # 12-34",
            "address_2": "Apto 501",
            "city": "Bogotá",
            "phone": "573001234567",
            "country_code": "co",
        },
    }
}


@pytest.mark.asyncio
async def test_medusa_happy_path_maps_full_payload_field_by_field():
    with respx.mock(base_url=BASE_URL) as r:
        route = r.get("/store/carts/cart_01ABC").mock(
            return_value=httpx.Response(200, json=_FULL_PAYLOAD)
        )
        snap = await _reader().get_cart("cart_01ABC")

        # request wire: path + header de auth de Store API
        assert route.called
        req = route.calls.last.request
        assert req.url.path == "/store/carts/cart_01ABC"
        assert req.headers["x-publishable-api-key"] == PUB_KEY

    assert snap is not None
    assert snap.cart_id == "cart_01ABC"
    assert snap.email == "cliente@test.com"
    assert snap.currency_code == "cop"
    assert snap.phone == "573001234567"
    assert snap.city == "Bogotá"
    assert snap.address == "Cra 7 # 12-34, Apto 501"
    assert snap.customer_name == "Ana Pardo"
    assert snap.items == (
        WebCartItem(
            product_title="Vela Ángel",
            quantity=2,
            product_handle="vela-angel",
            variant_title="Grande",
            unit_price=45000.0,
        ),
        WebCartItem(product_title="Velón Zodiacal", quantity=1),
    )


@pytest.mark.asyncio
async def test_medusa_404_returns_none():
    with respx.mock(base_url=BASE_URL) as r:
        r.get("/store/carts/cart_gone").mock(
            return_value=httpx.Response(404, json={"type": "not_found"})
        )
        assert await _reader().get_cart("cart_gone") is None


@pytest.mark.asyncio
async def test_medusa_500_returns_none():
    with respx.mock(base_url=BASE_URL) as r:
        r.get("/store/carts/cart_boom").mock(
            return_value=httpx.Response(500, text="internal error")
        )
        assert await _reader().get_cart("cart_boom") is None


@pytest.mark.asyncio
async def test_medusa_malformed_items_discarded_and_missing_address_is_none():
    payload = {
        "cart": {
            "id": "cart_01MIX",
            "items": [
                {"quantity": 3, "unit_price": 1000},  # sin product_title → fuera
                {"product_title": "Rosa", "quantity": None},  # quantity inválido → fuera
                {"product_title": "Vela Sobreviviente", "quantity": 1},
            ],
            # sin email, sin shipping_address
        }
    }
    with respx.mock(base_url=BASE_URL) as r:
        r.get("/store/carts/cart_01MIX").mock(
            return_value=httpx.Response(200, json=payload)
        )
        snap = await _reader().get_cart("cart_01MIX")
    assert snap is not None
    assert snap.items == (
        WebCartItem(product_title="Vela Sobreviviente", quantity=1),
    )
    assert snap.email is None
    assert snap.phone is None
    assert snap.city is None
    assert snap.address is None
    assert snap.customer_name is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shipping", "expected_address"),
    [
        ({"address_1": "Calle 1", "address_2": "Torre B"}, "Calle 1, Torre B"),
        ({"address_1": "Calle 1"}, "Calle 1"),  # sin address_2 → sin coma
    ],
    ids=["a1_y_a2_concatenados", "solo_a1"],
)
async def test_medusa_address_lines_concatenation(shipping, expected_address):
    payload = {"cart": {"id": "cart_01ADDR", "items": [], "shipping_address": shipping}}
    with respx.mock(base_url=BASE_URL) as r:
        r.get("/store/carts/cart_01ADDR").mock(
            return_value=httpx.Response(200, json=payload)
        )
        snap = await _reader().get_cart("cart_01ADDR")
    assert snap is not None
    assert snap.address == expected_address


@pytest.mark.asyncio
async def test_medusa_connect_error_propagates():
    """Regla 4 del ConnectorKit: transporte roto NO se disfraza de None."""
    with respx.mock(base_url=BASE_URL) as r:
        r.get("/store/carts/cart_net").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(httpx.ConnectError):
            await _reader().get_cart("cart_net")


# ---------------------------------------------------------------------------
# Composición — factory selection por env (patrón orders/composition.py).
# ---------------------------------------------------------------------------


@pytest.fixture()
def _fresh_composition(monkeypatch):
    """Aísla env + singletons lru_cache antes Y después (no envenenar suites
    vecinas que dependan de get_medusa_settings cacheado)."""
    from src.platform.medusa.composition import get_medusa_settings

    def _clear() -> None:
        get_web_cart_reader.cache_clear()
        get_medusa_settings.cache_clear()

    monkeypatch.setenv("MEDUSA_BASE_URL", "https://m.test")
    monkeypatch.delenv("MEDUSA_PUBLISHABLE_API_KEY", raising=False)
    _clear()
    yield monkeypatch
    _clear()


def test_composition_with_publishable_key_selects_medusa_reader(_fresh_composition):
    _fresh_composition.setenv("MEDUSA_PUBLISHABLE_API_KEY", "pk_live_abc")
    reader = get_web_cart_reader()
    assert isinstance(reader, MedusaStoreCartReader)


def test_composition_without_publishable_key_falls_back_to_null(_fresh_composition):
    reader = get_web_cart_reader()
    assert isinstance(reader, NullWebCartReader)


def test_composition_without_any_medusa_env_never_raises(_fresh_composition):
    """El proceso API local puede correr SIN env MEDUSA_* — la factory jamás
    rompe el boot: degrada a NullWebCartReader (ValidationError absorbida)."""
    _fresh_composition.delenv("MEDUSA_BASE_URL", raising=False)
    reader = get_web_cart_reader()
    assert isinstance(reader, NullWebCartReader)


# ---------------------------------------------------------------------------
# Settings — field nueva en MedusaSettings (patrón medusa/test_settings.py).
# ---------------------------------------------------------------------------


def test_settings_publishable_api_key_from_env(monkeypatch):
    from src.platform.medusa.settings import MedusaSettings

    monkeypatch.setenv("MEDUSA_BASE_URL", "https://m.test")
    monkeypatch.setenv("MEDUSA_PUBLISHABLE_API_KEY", "pk_env_1")
    s = MedusaSettings()
    # getattr: si la field no existe todavía, el rojo es un assert legible
    # (None != 'pk_env_1'), no un AttributeError críptico.
    assert getattr(s, "publishable_api_key", None) == "pk_env_1"


def test_settings_publishable_api_key_defaults_to_none(monkeypatch):
    from src.platform.medusa.settings import MedusaSettings

    monkeypatch.setenv("MEDUSA_BASE_URL", "https://m.test")
    monkeypatch.delenv("MEDUSA_PUBLISHABLE_API_KEY", raising=False)
    s = MedusaSettings()
    assert "publishable_api_key" in type(s).model_fields  # la field existe
    assert getattr(s, "publishable_api_key", "MISSING") is None
