"""`list_promotions` también cuenta las categorías de promoción del catálogo.

Incidente 2026-11-12: el operador creó la categoría «Promociones Navideñas» y
el bot no la ofrecía cuando le preguntaban por las promociones navideñas. El
asunto «promociones» lleva al bot a `list_promotions`, que solo leía los
cupones de Medusa y le decía «si no hay, no hay promociones». La marca de
promoción vive en Medusa (metadata de la categoría) y llega en el catálogo:
la tool la cuenta junto a los cupones y dice cómo mostrarla.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogUnavailableError
from src.platform.catalog.categories import CatalogCategoryDTO
from src.platform.promotions.port import (
    FakePromotionsPort,
    PromotionDTO,
    PromotionsUnavailableError,
)
from src.plugins.chats.agent.sales.tools.coupons import ListPromotionsTool

KEY = "wa_test_promo_categories"

_PROMO = CatalogCategoryDTO(
    slug="promociones", label="Promociones Navideñas", product_count=6, promotional=True
)
_PLAIN = CatalogCategoryDTO(slug="decorativas", label="Decorativas", product_count=19)


class _Catalog:
    def __init__(self, categories: list[CatalogCategoryDTO], *, fails: bool = False) -> None:
        self._categories = categories
        self._fails = fails

    async def list_categories(self) -> list[CatalogCategoryDTO]:
        if self._fails:
            raise CatalogUnavailableError("snapshot not found")
        return self._categories


class _DownPromotions:
    async def list_active(self):
        raise PromotionsUnavailableError("medusa down")


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _tool(vault, *, promotions=None, catalog=None) -> ListPromotionsTool:
    return ListPromotionsTool(
        workspace=str(vault),
        promotions=promotions or FakePromotionsPort([]),
        catalog=catalog,
    )


@pytest.mark.asyncio
async def test_promo_category_without_coupons_is_offered_not_denied(ctx, _isolate_vault_dir):
    tool = _tool(_isolate_vault_dir, catalog=_Catalog([_PLAIN, _PROMO]))
    env = json.loads(await tool.execute_with_context(ctx))

    assert env["promotions"] == []
    assert env["categories"] == [{"name": "Promociones Navideñas", "products": 6}]
    assert "no hay promociones" not in env["summary"].lower()
    assert "Promociones Navideñas" in env["summary"]
    assert "present_products" in env["summary"]


@pytest.mark.asyncio
async def test_without_coupons_nor_promo_categories_it_still_says_there_are_none(
    ctx, _isolate_vault_dir
):
    tool = _tool(_isolate_vault_dir, catalog=_Catalog([_PLAIN]))
    env = json.loads(await tool.execute_with_context(ctx))

    assert env["categories"] == []
    assert "no hay promociones" in env["summary"].lower()


@pytest.mark.asyncio
async def test_promo_category_is_offered_when_coupons_cannot_be_read(ctx, _isolate_vault_dir):
    tool = _tool(_isolate_vault_dir, promotions=_DownPromotions(), catalog=_Catalog([_PROMO]))
    env = json.loads(await tool.execute_with_context(ctx))

    assert env["error"] == "unavailable"
    assert env["categories"] == [{"name": "Promociones Navideñas", "products": 6}]
    assert "Promociones Navideñas" in env["summary"]


@pytest.mark.asyncio
async def test_unreadable_catalog_leaves_the_coupons_answer_as_before(ctx, _isolate_vault_dir):
    tool = _tool(_isolate_vault_dir, catalog=_Catalog([_PROMO], fails=True))
    env = json.loads(await tool.execute_with_context(ctx))

    assert env["categories"] == []
    assert "no hay promociones" in env["summary"].lower()


@pytest.mark.asyncio
async def test_coupons_and_promo_categories_are_offered_together(ctx, _isolate_vault_dir):
    coupon = PromotionDTO(
        id="promo_1", code="MAMA15", discount_type="percentage", value=15,
        currency_code="cop", target_type="items", allocation="across",
        max_quantity=None, product_ids=(), variant_ids=(), collection_ids=(),
        min_subtotal_cop=None, is_automatic=False, status="active",
        starts_at_ms=None, ends_at_ms=None, budget_type=None, budget_limit=None,
        budget_used=None, description="Día de la madre",
    )
    tool = _tool(
        _isolate_vault_dir,
        promotions=FakePromotionsPort([coupon]),
        catalog=_Catalog([_PROMO]),
    )
    env = json.loads(await tool.execute_with_context(ctx))

    assert [p["code"] for p in env["promotions"]] == ["MAMA15"]
    assert "MAMA15" in env["summary"]
    assert "Promociones Navideñas" in env["summary"]
