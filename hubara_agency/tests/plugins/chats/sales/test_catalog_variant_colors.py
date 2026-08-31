"""Envelope de catálogo con mapeo color↔signo (Duo Zodiacal).

Cada signo del Duo viene en UN color fijo, declarado por el operador en
`product.metadata["colores"]` (no existe en options/SKU/variantes — solo en
las fotos). El agente necesita ese mapeo en el detalle del producto para
poder responder "el rojo no está en Leo, pero SÍ en Aries" en vez de negar
el color o inventar la combinación.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.plugins.chats.agent.sales.tools.catalog import GetProductByHandleTool


def _duo(metadata: dict[str, str] | None) -> CatalogProductDTO:
    return CatalogProductDTO(
        id="prod_duo",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        metadata=metadata,
        options={"Signo": ["Aries", "Leo", "Escorpio"]},
        variants=[
            CatalogVariantDTO(
                id=f"v_{s.lower()}",
                title=s,
                options={"Signo": s},
                prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
            )
            for s in ("Aries", "Leo", "Escorpio")
        ],
    )


class _Cat:
    def __init__(self, product: CatalogProductDTO) -> None:
        self._product = product

    async def search(self, q, limit=10):
        raise NotImplementedError

    async def get_by_handle(self, handle):
        return self._product


def _ctx() -> ToolContext:
    return ToolContext(session_key="s", channel="whatsapp", chat_id="c")


@pytest.mark.asyncio
async def test_product_full_exposes_variant_colors(tmp_path: Path):
    product = _duo(
        {"colores": "Aries: rojo; Leo: naranja; Escorpio: negro"}
    )
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_Cat(product))
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    assert payload["product"]["variant_colors"] == {
        "Aries": ["rojo"],
        "Leo": ["naranja"],
        "Escorpio": ["negro"],
    }


@pytest.mark.asyncio
async def test_product_without_mapping_omits_key(tmp_path: Path):
    """Backward-compat: producto sin metadata `colores` → sin la key (no
    paga tokens ni sugiere un mapeo vacío)."""
    tool = GetProductByHandleTool(
        workspace=tmp_path, catalog=_Cat(_duo(None))
    )
    payload = json.loads(
        await tool.execute_with_context(_ctx(), handle="duo-zodiacal")
    )
    assert "variant_colors" not in payload["product"]
