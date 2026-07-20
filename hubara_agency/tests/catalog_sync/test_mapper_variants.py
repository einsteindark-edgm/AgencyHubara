"""Mapper Meta per-variante (caso Duo Zodiacal v2, 2026-07-15).

Un producto con options reales (eje "Signo", 12 variantes) debe llegar a
Meta como UN item por variante agrupado por `item_group_id` — así el
cliente ve y elige el signo (con SU foto) en la card/catálogo de WhatsApp.

Productos legacy (variante única "Unico" + tags) siguen mapeando a un
solo item con `retailer_id = product.id` — cero churn en el catálogo
existente.
"""
from __future__ import annotations

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.meta_catalog.client import _item_to_meta_data
from src.platform.meta_catalog.dtos import MetaCatalogItem
from src.platform.meta_catalog.mapper import map_products_batch


def _cop(amount: str) -> list[CatalogPriceDTO]:
    return [
        CatalogPriceDTO(
            amount=amount,
            currency_code="cop",
            min_quantity=None,
            max_quantity=None,
        )
    ]


_DUO_V2 = CatalogProductDTO(
    id="prod_duo_v2",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    description="Set de dos velas del signo que elijas.",
    thumbnail="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
    options={"Signo": ["Leo", "Escorpion"]},
    variants=[
        CatalogVariantDTO(
            id="v_leo",
            title="Leo",
            options={"Signo": "Leo"},
            prices=_cop("35000"),
        ),
        CatalogVariantDTO(
            id="v_esc",
            title="Escorpion",
            options={"Signo": "Escorpion"},
            prices=_cop("35000"),
        ),
    ],
    images=[
        CatalogImageDTO(
            url="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
            rank=0,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/Leo-01KXM9VDBFR9ZAGZ5JRQA1TN02.webp",
            rank=1,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/Escorpion-01KXM9VEE02SP6CSZE04JEC579.webp",
            rank=2,
        ),
    ],
)

_LEGACY = CatalogProductDTO(
    id="prod_legacy",
    handle="sacrificio-de-amor",
    title="Sacrificio de Amor",
    status="published",
    description="desc",
    thumbnail="https://assets.hubara.com.co/banner-01KN.webp",
    variants=[
        CatalogVariantDTO(id="v1", title="Unico", prices=_cop("19000"))
    ],
    images=[],
    tags=["Aroma: Lavanda"],
    categories=[],
)


def test_variant_product_maps_one_item_per_variant():
    items, skipped = map_products_batch([_DUO_V2])
    assert skipped == []
    assert len(items) == 2
    by_id = {i.retailer_id: i for i in items}
    assert set(by_id) == {"v_leo", "v_esc"}
    for item in items:
        assert item.item_group_id == "prod_duo_v2"
        assert item.price == "35000 COP"


def test_variant_item_uses_its_own_image_and_name():
    items, _ = map_products_batch([_DUO_V2])
    leo = next(i for i in items if i.retailer_id == "v_leo")
    esc = next(i for i in items if i.retailer_id == "v_esc")
    assert "Leo-01KXM9VDBFR9ZAGZ5JRQA1TN02" in leo.image_url
    assert "Escorpion-01KXM9VEE02SP6CSZE04JEC579" in esc.image_url
    assert leo.name == "Duo Zodiacal · Leo"
    assert esc.name == "Duo Zodiacal · Escorpion"


def test_variant_without_matching_image_falls_back_to_thumbnail():
    duo = CatalogProductDTO(
        id=_DUO_V2.id,
        handle=_DUO_V2.handle,
        title=_DUO_V2.title,
        status=_DUO_V2.status,
        description=_DUO_V2.description,
        thumbnail=_DUO_V2.thumbnail,
        options=_DUO_V2.options,
        variants=_DUO_V2.variants,
        images=[_DUO_V2.images[0]],  # solo la portada
    )
    items, _ = map_products_batch([duo])
    assert all("00-portada" in i.image_url for i in items)


def test_legacy_single_variant_product_unchanged():
    items, _ = map_products_batch([_LEGACY])
    assert len(items) == 1
    assert items[0].retailer_id == "prod_legacy"
    assert items[0].item_group_id is None
    assert items[0].name == "Sacrificio de Amor"


def test_serializer_includes_item_group_id():
    item = MetaCatalogItem(
        retailer_id="v_leo",
        name="Duo Zodiacal · Leo",
        description="desc",
        url="https://hubara.com.co/products/duo-zodiacal",
        image_url="https://img.example/leo.jpg",
        price="35000 COP",
        availability="in stock",
        item_group_id="prod_duo_v2",
    )
    data = _item_to_meta_data(item)
    assert data["item_group_id"] == "prod_duo_v2"


def test_first_variant_priceless_does_not_kill_product():
    """Premortem §4.1: si `variants[0]` no tiene precio pero otra variante
    sí, el producto NO se skipea entero — se publica con las variantes que
    tienen precio (gate viejo: `variants[0].prices` o nada)."""
    duo = CatalogProductDTO(
        id=_DUO_V2.id,
        handle=_DUO_V2.handle,
        title=_DUO_V2.title,
        status=_DUO_V2.status,
        description=_DUO_V2.description,
        thumbnail=_DUO_V2.thumbnail,
        options=_DUO_V2.options,
        variants=[
            CatalogVariantDTO(
                id="v_leo", title="Leo", options={"Signo": "Leo"}, prices=[]
            ),
            CatalogVariantDTO(
                id="v_esc",
                title="Escorpion",
                options={"Signo": "Escorpion"},
                prices=_cop("35000"),
            ),
        ],
        images=_DUO_V2.images,
    )
    items, skipped = map_products_batch([duo])
    assert skipped == []
    assert [i.retailer_id for i in items] == ["v_esc"]
    assert items[0].price == "35000 COP"


def test_variant_image_match_ignores_accents():
    """Premortem §4.7: option value con tilde ("Géminis") vs filename sin
    tilde ("Geminis-*.webp") deben matchear — sino la variante cae a la
    portada silenciosamente."""
    duo = CatalogProductDTO(
        id="prod_duo_v2",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        description="desc",
        thumbnail="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
        options={"Signo": ["Géminis", "Leo"]},
        variants=[
            CatalogVariantDTO(
                id="v_gem",
                title="Géminis",
                options={"Signo": "Géminis"},
                prices=_cop("35000"),
            ),
            CatalogVariantDTO(
                id="v_leo",
                title="Leo",
                options={"Signo": "Leo"},
                prices=_cop("35000"),
            ),
        ],
        images=[
            CatalogImageDTO(
                url="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
                rank=0,
            ),
            CatalogImageDTO(
                url="https://assets.hubara.com.co/Geminis-01KXM9VDDDDDDDDDDDDDDDDDDD.webp",
                rank=1,
            ),
        ],
    )
    items, _ = map_products_batch([duo])
    gem = next(i for i in items if i.retailer_id == "v_gem")
    assert "Geminis-01KXM9VDDDDDDDDDDDDDDDDDDD" in gem.image_url


def test_variant_items_declare_variant_axis():
    """WhatsApp colapsa items con el mismo `item_group_id` en UNA card y solo
    muestra el selector de variantes si cada item declara EN QUÉ difiere.
    Los canales renderizan los campos de variante NATIVOS (color/size/
    material/pattern) — el custom `additional_variant_attribute` no tiene
    render confirmado en WhatsApp (prod 2026-07-17: con solo el custom, la
    card desapareció del catálogo). El eje del producto va al campo nativo
    `color` + el custom como metadata."""
    items, _ = map_products_batch([_DUO_V2])
    by_id = {i.retailer_id: i for i in items}
    assert by_id["v_leo"].color == "Leo"
    assert by_id["v_esc"].color == "Escorpion"
    assert by_id["v_leo"].additional_variant_attribute == "Signo:Leo"
    assert by_id["v_esc"].additional_variant_attribute == "Signo:Escorpion"


def test_legacy_single_variant_has_no_color():
    items, _ = map_products_batch([_LEGACY])
    assert items[0].color is None


def test_serializer_emits_color_only_when_present():
    item = MetaCatalogItem(
        retailer_id="v_leo",
        name="Duo Zodiacal · Leo",
        description="desc",
        url="https://hubara.com.co/products/duo-zodiacal",
        image_url="https://img.example/leo.jpg",
        price="35000 COP",
        availability="in stock",
        color="Leo",
    )
    assert _item_to_meta_data(item)["color"] == "Leo"
    no_color = MetaCatalogItem(
        retailer_id="p1",
        name="Vela",
        description="desc",
        url="https://hubara.com.co/products/vela",
        image_url="https://img.example/x.jpg",
        price="23000 COP",
        availability="in stock",
    )
    assert "color" not in _item_to_meta_data(no_color)


def test_legacy_single_variant_has_no_variant_axis():
    items, _ = map_products_batch([_LEGACY])
    assert items[0].additional_variant_attribute is None


def test_serializer_includes_additional_variant_attribute():
    item = MetaCatalogItem(
        retailer_id="v_leo",
        name="Duo Zodiacal · Leo",
        description="desc",
        url="https://hubara.com.co/products/duo-zodiacal",
        image_url="https://img.example/leo.jpg",
        price="35000 COP",
        availability="in stock",
        item_group_id="prod_duo_v2",
        additional_variant_attribute="Signo:Leo",
    )
    data = _item_to_meta_data(item)
    assert data["additional_variant_attribute"] == "Signo:Leo"


def test_serializer_omits_variant_attribute_when_absent():
    item = MetaCatalogItem(
        retailer_id="p1",
        name="Vela",
        description="desc",
        url="https://hubara.com.co/products/vela",
        image_url="https://img.example/x.jpg",
        price="23000 COP",
        availability="in stock",
    )
    assert "additional_variant_attribute" not in _item_to_meta_data(item)


def test_variant_axis_sanitizes_reserved_separators():
    """"," y ":" son separadores del formato — un option value que los
    contenga corrompería el string entero en silencio."""
    duo = CatalogProductDTO(
        id="prod_x",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        description="desc",
        thumbnail=_DUO_V2.thumbnail,
        options={"Signo": ["Leo, el rey: fuego"]},
        variants=[
            CatalogVariantDTO(
                id="v_raro",
                title="Leo, el rey: fuego",
                options={"Signo": "Leo, el rey: fuego"},
                prices=_cop("35000"),
            ),
            CatalogVariantDTO(
                id="v_esc",
                title="Escorpion",
                options={"Signo": "Escorpion"},
                prices=_cop("35000"),
            ),
        ],
        images=[_DUO_V2.images[0]],
    )
    items, _ = map_products_batch([duo])
    raro = next(i for i in items if i.retailer_id == "v_raro")
    assert raro.additional_variant_attribute == "Signo:Leo el rey fuego"


def test_serializer_omits_item_group_id_when_absent():
    item = MetaCatalogItem(
        retailer_id="p1",
        name="Vela",
        description="desc",
        url="https://hubara.com.co/products/vela",
        image_url="https://img.example/x.jpg",
        price="23000 COP",
        availability="in stock",
    )
    assert "item_group_id" not in _item_to_meta_data(item)
