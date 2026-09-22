"""Carrusel de productos en la campaña — dominio puro + resolver de tarjetas.

El operador elige 2..10 productos del catálogo; la campaña sale por la
plantilla de carrusel con esa cantidad de tarjetas (foto del producto subida
a Meta, "nombre · precio", botón "Me interesa" → `ref: HUB-<sku>`).
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


def test_build_carousel_cards_en_el_orden_elegido_con_ref_del_sku() -> None:
    products = {
        "cubo-love": _product("cubo-love", "Cubo Love", "HUB-CUBOLOVE", cop="38000"),
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "HUB-BUDA"),
    }
    cards = build_carousel_cards(
        ["vela-buda", "cubo-love"],
        products,
        media_ids={"vela-buda": "M1", "cubo-love": "M2"},
    )
    assert [c.header_media_id for c in cards] == ["M1", "M2"]
    assert [c.body_text for c in cards] == ["Vela Buda Zen · $45.000", "Cubo Love · $38.000"]
    assert [c.quick_reply_payload for c in cards] == ["ref: HUB-BUDA", "ref: HUB-CUBOLOVE"]


def test_build_carousel_cards_exige_foto_para_cada_producto() -> None:
    products = {"vela-buda": _product("vela-buda", "Vela", "HUB-BUDA")}
    with pytest.raises(ValueError, match="vela-buda"):
        build_carousel_cards(["vela-buda"], products, media_ids={})


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
async def test_resolve_campaign_carousel_sube_cada_foto_una_vez_y_cachea(
    monkeypatch, _isolate_vault_dir
) -> None:
    from src.plugins.marketing import carousel as mod
    from src.plugins.marketing.campaign_store import CampaignStore

    products = {
        "vela-buda": _product("vela-buda", "Vela Buda Zen", "HUB-BUDA"),
        "cubo-love": _product("cubo-love", "Cubo Love", "HUB-CUBOLOVE"),
    }
    uploads: list[tuple[str, bytes, str]] = []

    async def fake_fetch(url: str) -> tuple[bytes, str]:
        return (b"JPEGBYTES-" + url.encode(), "image/jpeg")

    async def fake_upload(phone_number_id: str, content: bytes, mime: str) -> str:
        uploads.append((phone_number_id, content, mime))
        return f"MEDIA{len(uploads)}"

    monkeypatch.setattr(mod, "get_catalog_client", lambda: _FakeCatalog(products))
    monkeypatch.setattr(mod, "fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(mod, "upload_media", fake_upload)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")

    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["carousel_handles"] = ["vela-buda", "cubo-love"]
    CampaignStore(_isolate_vault_dir).save(campaign)

    cards = await mod.resolve_campaign_carousel(campaign, now_ms=10_000)
    assert [c.header_media_id for c in cards] == ["MEDIA1", "MEDIA2"]
    assert uploads[0][0] == "PHONE_ENV" and uploads[0][2] == "image/jpeg"
    # El media_id queda cacheado en la campaña (vale 30 días en Meta).
    saved = CampaignStore(_isolate_vault_dir).get("mkt-1")
    assert saved["carousel_media"]["vela-buda"]["media_id"] == "MEDIA1"

    # Segunda resolución (envío de prueba → envío real): cero re-subidas.
    cards_again = await mod.resolve_campaign_carousel(saved, now_ms=20_000)
    assert [c.header_media_id for c in cards_again] == ["MEDIA1", "MEDIA2"]
    assert len(uploads) == 2


@pytest.mark.asyncio
async def test_resolve_campaign_carousel_resube_si_el_media_vencio(
    monkeypatch, _isolate_vault_dir
) -> None:
    from src.plugins.marketing import carousel as mod
    from src.plugins.marketing.campaign_store import CampaignStore

    products = {"a": _product("a", "A", "HUB-A"), "b": _product("b", "B", "HUB-B")}
    uploads: list[str] = []

    async def fake_fetch(url: str):
        return (b"x", "image/jpeg")

    async def fake_upload(phone_number_id, content, mime):
        uploads.append(mime)
        return f"NEW{len(uploads)}"

    monkeypatch.setattr(mod, "get_catalog_client", lambda: _FakeCatalog(products))
    monkeypatch.setattr(mod, "fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(mod, "upload_media", fake_upload)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")

    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["carousel_handles"] = ["a", "b"]
    old = 1_000
    campaign["carousel_media"] = {
        "a": {"media_id": "OLD", "uploaded_at_ms": old},
        "b": {"media_id": "OLDB", "uploaded_at_ms": old},
    }
    CampaignStore(_isolate_vault_dir).save(campaign)
    cards = await mod.resolve_campaign_carousel(
        campaign, now_ms=old + mod.MEDIA_TTL_MS + 1
    )
    assert [c.header_media_id for c in cards] == ["NEW1", "NEW2"]


@pytest.mark.asyncio
async def test_resolve_campaign_carousel_producto_sin_foto_es_error_claro(
    monkeypatch, _isolate_vault_dir
) -> None:
    from src.plugins.marketing import carousel as mod
    from src.plugins.marketing.campaign_store import CampaignStore

    sin_foto = _product("a", "A", "HUB-A")
    sin_foto.thumbnail = None
    products = {"a": sin_foto, "b": _product("b", "B", "HUB-B")}
    monkeypatch.setattr(mod, "get_catalog_client", lambda: _FakeCatalog(products))
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["carousel_handles"] = ["a", "b"]
    CampaignStore(_isolate_vault_dir).save(campaign)
    with pytest.raises(mod.CarouselError, match="sin foto"):
        await mod.resolve_campaign_carousel(campaign, now_ms=1)
