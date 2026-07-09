"""Las tools de catálogo exponen los diseños derivados de los filenames.

Caso real (sesión wa_573125671604): el cliente pidió "un leo" y el LLM
respondió "no tengo variantes separadas por cada signo" — pero las fotos del
Duo Zodiacal en Medusa YA se llaman `leo-...webp`, `Acuario-...webp`, etc.
Estas tools deben exponer esa lista CERRADA de diseños (mismo patrón que
aromas/colors de parse_variant_tags) para que el LLM sepa qué existe.
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

_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    description=None,
    thumbnail="https://assets.hubara.com.co/1.%20aries-01KW2SQMAPCTQD74M3HK8DQ3AB.webp",
    variants=[
        CatalogVariantDTO(
            id="v1",
            title="Unico",
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        )
    ],
    images=[
        CatalogImageDTO(
            url="https://assets.hubara.com.co/1.%20aries-01KW2SQMAPCTQD74M3HK8DQ3AB.webp",
            rank=0,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/leo-01KW2SQSD4RP0KSM9HTJ38QPEF.webp",
            rank=1,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/cancer-01KW2SQP0Q0VFRJK20Y0N89KJ3.webp",
            rank=2,
        ),
        # Segunda foto del MISMO diseño — no debe duplicar "Cancer"
        CatalogImageDTO(
            url="https://assets.hubara.com.co/cancer2-01KW2SQPZTRRZ9B27G9KDXTSGG.webp",
            rank=3,
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
                version="v1", fetched_at="2026-07-08T00:00:00Z", product_count=1
            ),
            results=[_DUO],
        )

    async def get_by_handle(self, handle: str):
        return _DUO


def _ctx() -> ToolContext:
    return ToolContext(session_key="s", channel="whatsapp", chat_id="c")


@pytest.mark.asyncio
async def test_get_product_by_handle_exposes_designs(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    product = payload["product"]
    # Lista cerrada, en orden de rank, sin duplicados
    assert product["designs"] == ["Aries", "Leo", "Cancer"]
    # Cada imagen lleva su label (o None) para que el LLM pueda elegir
    labels = [img["label"] for img in product["images"]]
    assert labels == ["Aries", "Leo", "Cancer", "Cancer"]


@pytest.mark.asyncio
async def test_search_products_summary_exposes_designs(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    payload = json.loads(await tool.execute_with_context(_ctx(), q="zodiacal"))
    assert payload["results"][0]["designs"] == ["Aries", "Leo", "Cancer"]
