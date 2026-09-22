"""Carrusel de productos en la campaña — dominio puro + resolver de tarjetas.

El operador elige 2..10 productos del catálogo; la campaña sale por la
plantilla de carrusel con esa cantidad de product cards del catálogo de Meta
(`product_retailer_id` + META_CATALOG_ID: foto y precio los pone Meta).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.plugins.marketing.domain.campaigns import (
    build_carousel_cards,
    build_send_plan,
    campaign_template_name,
    carousel_card_body,
    new_campaign,
)


@dataclass
class _Price:
    amount: str
    currency_code: str


@dataclass
class _Variant:
    id: str
    title: str
    sku: str | None = None
    prices: list = field(default_factory=list)


@dataclass
class _Product:
    id: str
    handle: str
    title: str
    thumbnail: str | None = None
    variants: list = field(default_factory=list)
    status: str = "published"


def _product(handle: str, title: str, sku: str | None, cop: str = "45000") -> _Product:
    return _Product(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        thumbnail=f"https://cdn/{handle}.jpg",
        variants=[
            _Variant(
                id=f"var_{handle}",
                title="Default",
                sku=sku,
                prices=[_Price(amount="12", currency_code="usd"), _Price(amount=cop, currency_code="cop")],
            )
        ],
    )


class _Rate:
    class _Entry:
        usd_micros_per_message = 12_500

    rates = {"marketing": _Entry()}


# --- plantilla según cantidad de productos ---------------------------------


def test_campaign_template_name_usa_carrusel_solo_con_productos() -> None:
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    assert campaign_template_name(campaign) == "campaign_promo_marketing_v1"
    campaign["carousel_handles"] = ["a", "b", "c"]
    assert campaign_template_name(campaign) == "campaign_carousel_marketing_v1_3"


def test_build_send_plan_lleva_los_handles_y_la_plantilla_de_carrusel() -> None:
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["segments"] = ["clientes"]
    campaign["carousel_handles"] = ["vela-buda", "cubo-love"]
    plan = build_send_plan(campaign, [("wa_1", {"tag": "COMPRA_EXITOSA"})], _Rate())
    assert plan.template_name == "campaign_carousel_marketing_v1_2"
    assert plan.carousel_handles == ["vela-buda", "cubo-love"]


def test_build_send_plan_sin_productos_no_lleva_carrusel() -> None:
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["segments"] = ["clientes"]
    plan = build_send_plan(campaign, [("wa_1", {"tag": "COMPRA_EXITOSA"})], _Rate())
    assert plan.template_name == "campaign_promo_marketing_v1"
    assert plan.carousel_handles == []


# --- tarjetas ---------------------------------------------------------------


def test_carousel_card_body_es_nombre_precio_cop_en_una_linea_y_corto() -> None:
    product = _product("vela-buda", "Vela  Buda\nZen", "HUB-BUDA", cop="45000")
    assert carousel_card_body(product) == "Vela Buda Zen · $45.000"
    largo = _product("x", "V" * 200, "HUB-X")
    assert len(carousel_card_body(largo)) <= 160


def test_build_carousel_cards_son_product_cards_del_catalogo_de_meta() -> None:
    products = {
        "cubo-love": _product("cubo-love", "Cubo Love", "HUB-CUBOLOVE", cop="38000"),
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "HUB-BUDA"),
    }
    cards = build_carousel_cards(["vela-buda", "cubo-love"], products, catalog_id="868000000000000")
    assert [c.product_retailer_id for c in cards] == ["HUB-BUDA", "HUB-CUBOLOVE"]
    assert {c.catalog_id for c in cards} == {"868000000000000"}
    assert [c.body_text for c in cards] == ["Vela Buda Zen · $45.000", "Cubo Love · $38.000"]
    assert all(c.header_media_id is None and c.quick_reply_payload is None for c in cards)


def test_build_carousel_cards_exige_catalogo_de_meta_y_producto() -> None:
    products = {"vela-buda": _product("vela-buda", "Vela", "HUB-BUDA")}
    with pytest.raises(ValueError, match="META_CATALOG_ID"):
        build_carousel_cards(["vela-buda"], products, catalog_id="")
    with pytest.raises(ValueError, match="cubo-love"):
        build_carousel_cards(["cubo-love"], products, catalog_id="868")


# --- resolver (I/O con fakes) -----------------------------------------------


class _FakeCatalog:
    def __init__(self, products: dict[str, _Product]) -> None:
        self._products = products

    async def get_by_handle(self, handle: str):
        try:
            return self._products[handle]
        except KeyError as e:
            from src.platform.catalog.errors import ProductNotFoundError

            raise ProductNotFoundError(handle) from e


@pytest.mark.asyncio
async def test_resolve_campaign_carousel_arma_product_cards_sin_subir_nada(
    monkeypatch, _isolate_vault_dir
) -> None:
    from src.plugins.marketing import carousel as mod

    products = {
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "HUB-BUDA"),
        "cubo-love": _product("cubo-love", "Cubo Love", "HUB-CUBOLOVE"),
    }
    monkeypatch.setattr(mod, "get_catalog_client", lambda: _FakeCatalog(products))
    monkeypatch.setenv("META_CATALOG_ID", "868000000000000")

    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["carousel_handles"] = ["vela-buda", "cubo-love"]
    cards = await mod.resolve_campaign_carousel(campaign, now_ms=10_000)
    assert [c.product_retailer_id for c in cards] == ["HUB-BUDA", "HUB-CUBOLOVE"]
    assert {c.catalog_id for c in cards} == {"868000000000000"}


@pytest.mark.asyncio
async def test_resolve_campaign_carousel_sin_catalogo_meta_o_producto_es_error_claro(
    monkeypatch, _isolate_vault_dir
) -> None:
    from src.plugins.marketing import carousel as mod

    products = {"a": _product("a", "A", "HUB-A")}
    monkeypatch.setattr(mod, "get_catalog_client", lambda: _FakeCatalog(products))
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["carousel_handles"] = ["a", "b"]
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    with pytest.raises(mod.CarouselError, match="META_CATALOG_ID"):
        await mod.resolve_campaign_carousel(campaign, now_ms=1)
    monkeypatch.setenv("META_CATALOG_ID", "868")
    with pytest.raises(mod.CarouselError, match="'b'"):
        await mod.resolve_campaign_carousel(campaign, now_ms=1)
