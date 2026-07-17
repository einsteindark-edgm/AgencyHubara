"""Los intents que referencian Meta Catalog usan el retailer_id VIGENTE.

Bug de prod (2026-07-16, sesión wa_573125671604 post-#179): tras el push
per-variante, el item de Meta con `retailer_id = product.id` del Duo
Zodiacal YA NO EXISTE (fue reemplazado por 12 items `variant_...` con
`item_group_id`). El MPM (`interactive.product_list`) seguía referenciando
`product_retailer_id = p.id` → WhatsApp dropea el item silenciosamente y
el Duo desaparece del catálogo que ve el cliente.

Regla: producto con variantes reales → el retailer_id en Meta es el de UNA
variante (la primera, determinista). Producto legacy → product.id, igual
que siempre.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import ProductNotFoundError
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentOrderConfirmationTool,
    PresentProductsTool,
)

_DUO_V2 = CatalogProductDTO(
    id="prod_duo_v2",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    description="Set de dos velas.",
    thumbnail="https://assets.hubara.com.co/00-portada-x.webp",
    options={"Signo": ["Leo", "Escorpion"]},
    variants=[
        CatalogVariantDTO(
            id="v_leo",
            title="Leo",
            options={"Signo": "Leo"},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        ),
        CatalogVariantDTO(
            id="v_esc",
            title="Escorpion",
            options={"Signo": "Escorpion"},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        ),
    ],
    images=[CatalogImageDTO(url="https://assets.hubara.com.co/Leo-x.webp", rank=0)],
)

_LEGACY = CatalogProductDTO(
    id="prod_legacy",
    handle="luz-serena",
    title="Luz Serena",
    status="published",
    thumbnail="https://assets.hubara.com.co/luz.webp",
    variants=[
        CatalogVariantDTO(
            id="v_unico",
            title="Unico",
            prices=[CatalogPriceDTO(amount="23000", currency_code="cop")],
        )
    ],
)


class _FakeCatalog:
    def __init__(self, products: list[CatalogProductDTO]) -> None:
        self._by_handle = {p.handle: p for p in products}

    async def get_by_handle(self, handle: str):
        if handle not in self._by_handle:
            raise ProductNotFoundError(handle)
        return self._by_handle[handle]


def _ctx() -> ToolContext:
    return ToolContext(session_key="s_test", channel="whatsapp", chat_id="c")


def _read_intents(tmp_path: Path, session_key: str) -> list[dict]:
    data = json.loads(
        (tmp_path / "isolated_vault" / session_key / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    return data["pending_ui_intents"]


@pytest.mark.asyncio
async def test_products_list_uses_variant_retailer_for_option_products(
    tmp_path: Path,
):
    tool = PresentProductsTool(
        workspace=str(tmp_path),
        catalog=_FakeCatalog([_DUO_V2, _LEGACY]),
    )
    result = json.loads(
        await tool.execute_with_context(
            _ctx(),
            handles=["duo-zodiacal", "luz-serena"],
            intro_text="Catálogo:",
            group_by="none",
        )
    )
    assert result["queued"] is True

    intents = _read_intents(tmp_path, "s_test")
    rows = [
        r
        for s in intents[0]["params"]["sections"]
        for r in s["rows"]
    ]
    by_handle = {r["id"]: r for r in rows}
    # Producto con variantes reales → retailer de la PRIMERA variante
    assert by_handle["duo-zodiacal"]["product_retailer_id"] == "v_leo"
    # Legacy → product.id como siempre
    assert by_handle["luz-serena"]["product_retailer_id"] == "prod_legacy"


@pytest.mark.asyncio
async def test_order_confirmation_uses_variant_retailer(tmp_path: Path):
    tool = PresentOrderConfirmationTool(
        workspace=str(tmp_path),
        catalog=_FakeCatalog([_DUO_V2, _LEGACY]),
    )
    result = json.loads(
        await tool.execute_with_context(
            _ctx(),
            items=[
                {
                    "handle": "duo-zodiacal",
                    "quantity": 1,
                    "unit_price_cop": 35000,
                },
                {
                    "handle": "luz-serena",
                    "quantity": 1,
                    "unit_price_cop": 23000,
                },
            ],
            shipping_cop=8000,
            shipping_address_summary="Calle 1 # 2-3, Bogotá",
            payment_method="transfer",
        )
    )
    assert result.get("queued") is True, result

    intents = _read_intents(tmp_path, "s_test")
    items = intents[0]["params"]["items"]
    by_handle = {i["handle"]: i for i in items}
    assert by_handle["duo-zodiacal"]["retailer_id"] == "v_leo"
    assert by_handle["luz-serena"]["retailer_id"] == "prod_legacy"
