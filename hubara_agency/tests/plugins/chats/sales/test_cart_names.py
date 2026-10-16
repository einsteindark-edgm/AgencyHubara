"""El carrito del catálogo de WhatsApp llega con los nombres del catálogo.

Meta manda cada ítem del carrito con su `product_retailer_id` (el SKU, o el id
de la variante mientras no tenga SKU) y el bot veía solo eso:
«[el cliente armó un carrito con: 1× HUB-TRILOGIA]» (11 carritos en 9
conversaciones desde el 2026-09-10). El operador tampoco sabía en Chats qué
había pedido el cliente. Ahora cada ítem sale con el nombre, la variante y el
precio del catálogo (nunca el precio que trae Meta), y el turno lleva una nota
con el handle de cada producto para seguir la venta sin buscarlo.
"""
from __future__ import annotations

from tests.metadata_store_fakes import MergingMetadataStoreMixin

from typing import Any

import pytest

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.plugins.chats.agent.sales.cart_lines import build_cart_note, lines_from_products
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import translate_to_effective_text
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

TRILOGIA = CatalogProductDTO(
    id="prod_trilogia", handle="trilogia-del-terror", title="Trilogía del Terror", status="published",
    variants=[CatalogVariantDTO(id="variant_trilogia", title="Unico", sku="HUB-TRILOGIA",
                                prices=[CatalogPriceDTO(amount="95900", currency_code="cop")])],
)
DUO = CatalogProductDTO(
    id="prod_duo", handle="duo-zodiacal", title="Duo Zodiacal", status="published",
    options={"Signo": ["Leo", "Libra"]},
    variants=[
        CatalogVariantDTO(id="variant_leo", title="Leo", sku="HUB-DUO-LEO",
                          prices=[CatalogPriceDTO(amount="49500", currency_code="cop")]),
        CatalogVariantDTO(id="variant_libra", title="Libra", sku="HUB-DUO-LIBRA",
                          prices=[CatalogPriceDTO(amount="49500", currency_code="cop")]),
    ],
)
ITEMS = [
    {"product_retailer_id": "HUB-TRILOGIA", "quantity": 2, "item_price": 45000, "currency": "COP"},
    {"product_retailer_id": "variant_leo", "quantity": 1, "item_price": 49500, "currency": "COP"},
]


def _cart(items: list[dict[str, Any]], text: str | None = None) -> WhatsAppMessage:
    order: dict[str, Any] = {"catalog_id": "CATALOG_TEST", "product_items": items}
    if text:
        order["text"] = text
    return WhatsAppMessage(
        message_id="wamid.CART", from_number="573001234567", phone_number_id="PID",
        text=None, media=None, timestamp="1714312345", order=order,
    )


async def test_each_item_names_the_product_its_variant_and_the_catalog_price() -> None:
    lines = lines_from_products(ITEMS, [TRILOGIA, DUO])

    effective = await translate_to_effective_text(_cart(ITEMS), cart_lines=lines)

    assert effective.text == (
        "[el cliente armó un carrito con: 2× Trilogía del Terror a $95.900 c/u (HUB-TRILOGIA); "
        "1× Duo Zodiacal · Leo a $49.500 c/u (variant_leo)]"
    )


async def test_an_item_the_catalog_does_not_have_says_so() -> None:
    items = [{"product_retailer_id": "HUB-VIEJO", "quantity": 1}]

    effective = await translate_to_effective_text(_cart(items), cart_lines=lines_from_products(items, [TRILOGIA]))

    assert effective.text == "[el cliente armó un carrito con: 1× HUB-VIEJO (no está en el catálogo)]"


async def test_without_the_catalog_the_cart_reads_as_before() -> None:
    effective = await translate_to_effective_text(_cart(ITEMS, text="para regalo"))

    assert effective.text == '[el cliente armó un carrito con: 2× HUB-TRILOGIA, 1× variant_leo] "para regalo"'


def test_the_note_gives_the_handle_of_each_product_to_follow_the_sale() -> None:
    note = build_cart_note(ITEMS, lines_from_products(ITEMS, [TRILOGIA, DUO]))

    assert note is not None
    assert "2× «Trilogía del Terror» (handle trilogia-del-terror)" in note
    assert "1× «Duo Zodiacal», variante «Leo» (handle duo-zodiacal)" in note
    assert build_cart_note(ITEMS, None) is None


# --- el ingest -------------------------------------------------------------


class _Catalog:
    def __init__(self, products: list[CatalogProductDTO] | None = None, *, fails: bool = False) -> None:
        self.products = products or []
        self.fails = fails

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> Any:
        if self.fails:
            raise RuntimeError("snapshot caído")
        products = self.products

        class _Result:
            results = products

        return _Result()


class _History:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        self.texts.append(content)


class _Loader:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kw: Any) -> None:
        self.calls.append(kw)


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def read(self, session_id: str) -> dict[str, Any]:
        return dict(self.data.get(session_id, {}))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.data[session_id] = dict(data)

    def update(self, session_id: str, mutator: Any) -> dict[str, Any] | None:
        fresh = self.read(session_id)
        out = mutator(fresh)
        if out is not None:
            self.write(session_id, out)
        return out


def _ingest(catalog: _Catalog, history: _History, loader: _Loader) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        catalog=catalog,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_bot_and_the_operator_read_the_cart_by_name() -> None:
    history, loader = _History(), _Loader()

    await _ingest(_Catalog([TRILOGIA, DUO]), history, loader).execute(_cart(ITEMS))

    named = "2× Trilogía del Terror a $95.900 c/u (HUB-TRILOGIA)"
    assert named in history.texts[-1]
    assert named in loader.calls[-1]["message"]
    notes = loader.calls[-1]["extra_context"] or []
    assert any("(handle trilogia-del-terror)" in note for note in notes)


@pytest.mark.asyncio
async def test_with_the_catalog_down_the_cart_still_arrives_as_before() -> None:
    history, loader = _History(), _Loader()

    await _ingest(_Catalog(fails=True), history, loader).execute(_cart(ITEMS))

    assert loader.calls[-1]["message"] == "[el cliente armó un carrito con: 2× HUB-TRILOGIA, 1× variant_leo]"


# --- lo que se cobra por caja (caso del 2026-10-16) ---------------------------
#
# El carrito de una caja de Velas de deseos cuadradas ($25.000 la caja de 12)
# le llegaba al bot como «1× Velas de deseos cuadradas · Multicolor a $25.000
# c/u», y el bot le confirmó al cliente que cada vela valía $25.000. Lo que la
# tienda cobra por caja (`domain.yaml: sold_by_box`) llega con su caja.

DESEOS = CatalogProductDTO(
    id="prod_deseos", handle="velas-de-deseos-cuadradas", title="Velas de deseos cuadradas", status="published",
    options={"Color": ["Rojo y verde", "Multicolor"]},
    variants=[
        CatalogVariantDTO(id="variant_multi", title="Multicolor", sku="HUB-DESEOS-MULTICOLOR",
                          prices=[CatalogPriceDTO(amount="25000", currency_code="cop")]),
        CatalogVariantDTO(id="variant_rv", title="Rojo y verde", sku="HUB-DESEOS-RV",
                          prices=[CatalogPriceDTO(amount="25000", currency_code="cop")]),
    ],
)
BOX_ITEMS = [{"product_retailer_id": "HUB-DESEOS-MULTICOLOR", "quantity": 1, "item_price": 25000, "currency": "COP"}]
SOLD_BY_BOX = [{"producto": "velas-de-deseos-cuadradas", "por_caja": 12}]


async def test_what_is_sold_by_box_arrives_with_its_box_not_per_unit() -> None:
    lines = lines_from_products([*BOX_ITEMS, ITEMS[0]], [DESEOS, TRILOGIA], boxes=SOLD_BY_BOX)

    effective = await translate_to_effective_text(_cart([*BOX_ITEMS, ITEMS[0]]), cart_lines=lines)

    assert effective.text == (
        "[el cliente armó un carrito con: 1× Velas de deseos cuadradas · Multicolor a $25.000 la caja de 12 "
        "unidades (HUB-DESEOS-MULTICOLOR); 2× Trilogía del Terror a $95.900 c/u (HUB-TRILOGIA)]"
    )


def test_the_note_says_the_quantity_is_boxes() -> None:
    note = build_cart_note(BOX_ITEMS, lines_from_products(BOX_ITEMS, [DESEOS], boxes=SOLD_BY_BOX))

    assert note is not None
    assert (
        "1× «Velas de deseos cuadradas», variante «Multicolor» (handle velas-de-deseos-cuadradas): se cobra por "
        "caja de 12 unidades, el precio es el de la caja y la cantidad son cajas"
    ) in note


@pytest.mark.asyncio
async def test_the_ingest_reads_what_the_store_sells_by_box(monkeypatch) -> None:
    from src.plugins.chats.shared import store_pack

    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    store_pack.reset()
    history, loader = _History(), _Loader()
    try:
        await _ingest(_Catalog([DESEOS]), history, loader).execute(_cart(BOX_ITEMS))
    finally:
        store_pack.reset()

    assert "1× Velas de deseos cuadradas · Multicolor a $25.000 la caja de 12 unidades" in loader.calls[-1]["message"]
    assert "c/u" not in loader.calls[-1]["message"]
