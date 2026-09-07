"""Tools de catálogo del connector: envelopes JSON cerrados sobre el CatalogPort del SDK."""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.platform.catalog.categories import CatalogCategoryDTO, CategoryResolution
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import CatalogUnavailableError, ProductNotFoundError
from src.plugins.mba.tools.catalog import get_product_by_handle, list_categories, search_products

_MANIFEST = CatalogManifestDTO(version="v1", fetched_at="2026-09-07T00:00:00Z", product_count=2)

_VELA = CatalogProductDTO(
    id="prod_1", handle="vela-lavanda", title="Vela Lavanda", status="published",
    description="Cera de soya.", thumbnail="https://cdn/vela.webp",
    variants=[CatalogVariantDTO(id="var_1", title="Unico", prices=[
        CatalogPriceDTO(amount="9.00", currency_code="usd"), CatalogPriceDTO(amount="35000", currency_code="cop"),
    ])],
    images=[CatalogImageDTO(url="https://cdn/vela/img1.webp", rank=0)],
    tags=["Aroma: Lavanda", "Aroma: Vainilla", "Color: Blanco"],
    categories=["velas-aromaticas"], category_labels={"velas-aromaticas": "Velas Aromáticas"},
)
_ZODIAC = CatalogProductDTO(
    id="prod_2", handle="duo-zodiacal", title="Duo Zodiacal", status="published",
    variants=[
        CatalogVariantDTO(id="var_leo", title="Leo", prices=[CatalogPriceDTO(amount="52000", currency_code="cop")], options={"Signo": "Leo"}),
        CatalogVariantDTO(id="var_aries", title="Aries", prices=[CatalogPriceDTO(amount="52000", currency_code="cop")], options={"Signo": "Aries"}),
    ],
    images=[CatalogImageDTO(url="https://cdn/z/00-portada-duo.webp", rank=0), CatalogImageDTO(url="https://cdn/z/leo-1.webp", rank=1), CatalogImageDTO(url="https://cdn/z/aries-1.webp", rank=2)],
    tags=[], categories=["zodiacal"], metadata={"colores": "Leo: naranja; Aries: rojo"},
    options={"Signo": ["Leo", "Aries"]},
)


class _FakeCatalog:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple] = []

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> SearchResult:
        self.calls.append(("search", q, limit, category))
        if not self.available:
            raise CatalogUnavailableError("snapshot ausente")
        results = [_VELA, _ZODIAC][:limit]
        resolution = None
        if category is not None:
            matched = CatalogCategoryDTO(slug="velas-aromaticas", label="Velas Aromáticas", product_count=1) if "arom" in category else None
            resolution = CategoryResolution(query=category, matched=matched, confidence="partial" if matched else "none")
            if matched:
                results = [_VELA]
        return SearchResult(query=q, count=len(results), truncated=False, stale=False, manifest=_MANIFEST, results=results, category=resolution)

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        if not self.available:
            raise CatalogUnavailableError("snapshot ausente")
        for p in (_VELA, _ZODIAC):
            if p.handle == handle:
                return p
        raise ProductNotFoundError(handle)

    async def list_categories(self) -> list[CatalogCategoryDTO]:
        if not self.available:
            raise CatalogUnavailableError("snapshot ausente")
        return [CatalogCategoryDTO("velas-aromaticas", "Velas Aromáticas", 1), CatalogCategoryDTO("zodiacal", "Zodiacal", 1)]


@pytest.mark.asyncio
async def test_search_returns_closed_lists_cop_price_and_clamps_limit() -> None:
    cat = _FakeCatalog()
    out = await search_products(cat, q="vela", limit=99)
    assert cat.calls == [("search", "vela", 30, None)]
    assert out["count"] == 2 and out["truncated"] is False
    vela = out["results"][0]
    assert vela["handle"] == "vela-lavanda" and vela["price"] == "35000" and vela["currency"] == "cop"
    assert vela["aromas"] == ["Lavanda", "Vainilla"] and vela["colors"] == ["Blanco"]
    assert vela["categories"] == ["Velas Aromáticas"] and vela["variants"] == []
    zod = out["results"][1]
    assert zod["designs"] == ["Leo", "Aries"]  # la portada NO es un diseño
    assert zod["variants"] == [{"id": "var_leo", "title": "Leo"}, {"id": "var_aries", "title": "Aries"}]
    # límite mínimo 1 y por defecto 10
    await search_products(cat, q="", limit=0)
    await search_products(cat, q="")
    assert [c[2] for c in cat.calls[1:]] == [1, 10]


@pytest.mark.asyncio
async def test_search_reports_how_the_category_resolved_and_the_closed_list_when_it_did_not() -> None:
    cat = _FakeCatalog()
    ok = await search_products(cat, q="", category="aromaticas")
    assert ok["category"] == {"query": "aromaticas", "matched": "Velas Aromáticas", "confidence": "partial"}
    assert [p["handle"] for p in ok["results"]] == ["vela-lavanda"]
    miss = await search_products(cat, q="", category="lamparas")
    assert miss["category"]["matched"] is None
    assert miss["category"]["available"] == ["Velas Aromáticas", "Zodiacal"]
    assert "message" in miss["category"]


@pytest.mark.asyncio
async def test_catalog_down_is_an_explicit_error_the_agent_can_act_on() -> None:
    cat = _FakeCatalog(available=False)
    for out in (
        await search_products(cat, q="x"),
        await list_categories(cat),
        await get_product_by_handle(cat, handle="vela-lavanda"),
    ):
        assert out["error"] == "catalog_unavailable" and "colega" in out["message"]
    assert (await search_products(None, q="x"))["error"] == "catalog_unavailable"


@pytest.mark.asyncio
async def test_list_categories_is_the_closed_list() -> None:
    out = await list_categories(_FakeCatalog())
    assert out == {"count": 2, "categories": [
        {"name": "Velas Aromáticas", "product_count": 1}, {"name": "Zodiacal", "product_count": 1},
    ]}


@pytest.mark.asyncio
async def test_get_product_by_handle_full_detail_or_not_found() -> None:
    cat = _FakeCatalog()
    out = await get_product_by_handle(cat, handle="duo-zodiacal")
    assert out["found"] is True
    p = out["product"]
    assert p["options"] == {"Signo": ["Leo", "Aries"]}
    assert p["variants"][0] == {"id": "var_leo", "title": "Leo", "sku": None, "price": "52000", "currency": "cop", "options": {"Signo": "Leo"}}
    assert p["images"][1] == {"url": "https://cdn/z/leo-1.webp", "rank": 1, "label": "Leo"}
    assert p["variant_colors"] == {"Leo": ["naranja"], "Aries": ["rojo"]}
    vela = (await get_product_by_handle(cat, handle="vela-lavanda"))["product"]
    assert vela["description"] == "Cera de soya." and "variant_colors" not in vela
    miss = await get_product_by_handle(cat, handle="inventado")
    assert miss["found"] is False and "search_products" in miss["message"]


def test_fixtures_are_frozen_dtos() -> None:  # sanity: el fake usa los DTOs reales del port
    assert replace(_VELA, title="x").title == "x"


@pytest.mark.asyncio
async def test_handle_outside_the_slug_alphabet_never_reaches_the_port() -> None:
    class _Spy(_FakeCatalog):
        async def get_by_handle(self, handle: str):
            raise AssertionError(f"el port NO debe recibir {handle!r}")

    for bad in ("../../etc/passwd", "vela lavanda", "Vela", "x" * 200, ""):
        out = await get_product_by_handle(_Spy(), handle=bad)
        assert out["found"] is False, bad


@pytest.mark.asyncio
async def test_unavailable_detail_is_a_closed_code_not_an_internal_path() -> None:
    class _Down(_FakeCatalog):
        async def search(self, q, *, limit=10, category=None):
            raise CatalogUnavailableError("snapshot not found at /app/hubara_vault/catalog/snapshot.json")

    out = await search_products(_Down(), q="x")
    assert out["detail"] == "catalog_unavailable" and "/app/" not in str(out)
