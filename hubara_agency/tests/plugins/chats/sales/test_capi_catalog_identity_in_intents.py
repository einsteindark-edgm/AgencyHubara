"""Los intents que muestran producto llevan el `retailer_id` VIGENTE en Meta
(SKU) para que el evento CAPI correspondiente (ViewContent) cruce con el
catálogo en Commerce Manager (2026-09-14: coincidencia 0% porque los eventos
no traían identidad de producto)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentProductDetailTool,
    PresentProductGalleryTool,
)


class _Variant:
    def __init__(self, sku: str | None) -> None:
        self.sku = sku
        self.id = "variant_medusa_1"
        self.prices: list = []
        self.title = "Unico"
        self.options: dict = {}


class _Image:
    def __init__(self, url: str, rank: int = 0) -> None:
        self.url = url
        self.rank = rank


class _Product:
    def __init__(self, sku: str | None = "HUB-CUBOLOVE") -> None:
        self.id = "prod_medusa_1"
        self.handle = "cubo-love"
        self.title = "Cubo Love"
        self.thumbnail = "https://assets.hubara.com.co/cubo.webp"
        self.images = [_Image(self.thumbnail, 0), _Image("https://assets.hubara.com.co/cubo-2.webp", 1)]
        self.variants = [_Variant(sku)]
        self.options: dict = {}
        self.categories: list = []
        self.tags: list[str] = []
        self.price = "21000"
        self.currency = "cop"
        self.in_stock = True
        self.description = "Una vela cúbica."
        self.metadata = None


class _Catalog:
    def __init__(self, product: _Product) -> None:
        self._p = product

    async def get_by_handle(self, handle: str):
        if handle != self._p.handle:
            from src.platform.catalog import ProductNotFoundError

            raise ProductNotFoundError(handle)
        return self._p


@pytest.fixture
def ctx() -> ToolContext:
    key = "wa_test_capi_identity"
    return ToolContext(session_key=key, channel="whatsapp", chat_id=key)


@pytest.fixture
def vault(tmp_path: Path, ctx: ToolContext) -> Path:
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return vault


def _intents(vault: Path, key: str) -> list[dict]:
    return json.loads((vault / key / "metadata.json").read_text(encoding="utf-8")).get("pending_ui_intents", [])


@pytest.mark.asyncio
async def test_product_detail_intent_carries_meta_retailer_id(ctx, tmp_path, vault):
    tool = PresentProductDetailTool(workspace=str(tmp_path), catalog=_Catalog(_Product()))
    await tool.execute_with_context(ctx, handle="cubo-love")
    intent = _intents(vault, ctx.session_key)[0]
    assert intent["kind"] == "product_detail"
    assert intent["params"]["retailer_id"] == "HUB-CUBOLOVE"


@pytest.mark.asyncio
async def test_product_detail_without_sku_falls_back_to_medusa_id(ctx, tmp_path, vault):
    tool = PresentProductDetailTool(workspace=str(tmp_path), catalog=_Catalog(_Product(sku=None)))
    await tool.execute_with_context(ctx, handle="cubo-love")
    assert _intents(vault, ctx.session_key)[0]["params"]["retailer_id"] == "prod_medusa_1"


@pytest.mark.asyncio
async def test_product_gallery_intent_carries_meta_retailer_id(ctx, tmp_path, vault):
    tool = PresentProductGalleryTool(workspace=str(tmp_path), catalog=_Catalog(_Product()))
    await tool.execute_with_context(ctx, handle="cubo-love")
    intent = _intents(vault, ctx.session_key)[0]
    assert intent["kind"] == "product_gallery"
    assert intent["params"]["retailer_id"] == "HUB-CUBOLOVE"
