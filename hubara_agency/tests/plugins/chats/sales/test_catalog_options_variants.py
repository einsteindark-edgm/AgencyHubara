"""Tools de catálogo con productos de variantes REALES (Duo Zodiacal v2).

Desde 2026-07-15 el Duo Zodiacal vive en Medusa con option "Signo" y una
variante por signo (12), con la foto de cada signo nombrada por su valor
(`Leo-*.webp`) más una portada (`00-portada-*.webp`).

Contratos que el agente necesita:

1. `get_product_by_handle` expone `options` (el eje de selección con su
   closed-list) y el option value de cada variante.
2. `designs` se filtra contra los option values cuando el producto tiene
   variantes reales → la portada NO es un diseño ofrecible.
3. El precio por variante prefiere COP en multi-currency (mismo fix que
   `_first_price`, run 33a8dd9f mostró `currency: usd` en el detalle).
4. Productos legacy (variante única + tags) siguen igual (designs por
   filename, options None).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)

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
            prices=[
                CatalogPriceDTO(amount="35000", currency_code="usd"),
                CatalogPriceDTO(amount="35000", currency_code="cop"),
            ],
        ),
        CatalogVariantDTO(
            id="v_esc",
            title="Escorpion",
            options={"Signo": "Escorpion"},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
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


class _FakeCatalog:
    async def search(self, q: str, limit: int = 10):
        return SearchResult(
            query=q,
            count=1,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1",
                fetched_at="2026-07-15T00:00:00Z",
                product_count=1,
            ),
            results=[_DUO_V2],
        )

    async def get_by_handle(self, handle: str):
        return _DUO_V2


def _ctx() -> ToolContext:
    return ToolContext(session_key="s", channel="whatsapp", chat_id="c")


@pytest.mark.asyncio
async def test_product_full_exposes_options(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    product = payload["product"]
    assert product["options"] == {"Signo": ["Leo", "Escorpion"]}
    variants = {v["title"]: v for v in product["variants"]}
    assert variants["Leo"]["options"] == {"Signo": "Leo"}
    assert variants["Escorpion"]["options"] == {"Signo": "Escorpion"}


@pytest.mark.asyncio
async def test_designs_filtered_by_option_values(tmp_path: Path):
    """La portada no es un diseño: solo labels que son option values."""
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    assert payload["product"]["designs"] == ["Leo", "Escorpion"]


@pytest.mark.asyncio
async def test_search_summary_designs_filtered_too(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(await tool.execute_with_context(_ctx(), q="duo"))
    assert payload["results"][0]["designs"] == ["Leo", "Escorpion"]


@pytest.mark.asyncio
async def test_designs_filter_ignores_accents(tmp_path: Path):
    """Premortem §4.7: option "Géminis" (con tilde) vs label de filename
    "Geminis" (sin tilde) deben matchear en el filtro de designs — sino el
    diseño desaparece de la lista cerrada aunque exista la foto."""
    duo = CatalogProductDTO(
        id="p",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        options={"Signo": ["Géminis", "Leo"]},
        variants=[
            CatalogVariantDTO(
                id="v1",
                title="Géminis",
                options={"Signo": "Géminis"},
                prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
            ),
            CatalogVariantDTO(
                id="v2",
                title="Leo",
                options={"Signo": "Leo"},
                prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
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
            CatalogImageDTO(
                url="https://assets.hubara.com.co/Leo-01KXM9VDBFR9ZAGZ5JRQA1TN02.webp",
                rank=2,
            ),
        ],
    )

    class _Cat:
        async def search(self, q, limit=10):
            raise NotImplementedError

        async def get_by_handle(self, handle):
            return duo

    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_Cat())
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    # "Geminis" (label del filename) sobrevive el filtro; la portada no.
    assert payload["product"]["designs"] == ["Geminis", "Leo"]


@pytest.mark.asyncio
async def test_search_summary_includes_variant_ids_for_option_products(
    tmp_path: Path,
):
    """El carrito inbound de WhatsApp trae `variant_...` como retailer_id
    (post per-variante en Meta). El LLM solo puede resolverlo si el envelope
    del search trae los ids de variante — lista liviana {id, title} SOLO
    para productos con options (legacy no la necesita y no paga tokens)."""
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(await tool.execute_with_context(_ctx(), q="duo"))
    summary = payload["results"][0]
    assert summary["variants"] == [
        {"id": "v_leo", "title": "Leo"},
        {"id": "v_esc", "title": "Escorpion"},
    ]


@pytest.mark.asyncio
async def test_variant_price_prefers_cop(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    leo = next(
        v for v in payload["product"]["variants"] if v["title"] == "Leo"
    )
    assert leo["currency"] == "cop"
    assert leo["price"] == "35000"
