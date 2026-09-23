"""El pull del catálogo conserva las medidas de Medusa (caso 2026-09-22).

Un lead preguntó "¿Qué medidas tienen las velas?" y el bot respondió que el
catálogo no las traía. Medusa SÍ las tiene (height/width del producto: la web
muestra "Alto: 9 cm / Ancho: 6 cm" de la Calabaza) y el cliente de Medusa las
pide, pero `_to_dto` las descartaba: el snapshot que consulta el LLM nunca las
tuvo. Misma regla que la web (hubara_frontend `getProductDimensions`): nivel
producto primero, si no, la primera variante que traiga alguna medida. A
diferencia de la web, 0 = sin dato (la Trilogía muestra "0 cm" en la web).
"""
from __future__ import annotations

import json

import pytest

from src.plugins.catalog.agent.contracts import CatalogSyncInput
from src.plugins.catalog.agent.use_cases.pull_catalog import PullCatalogUseCase


class _FakeClient:
    def __init__(self, products: list[dict]) -> None:
        self._products = products

    async def iter_products(self, **kwargs):
        for p in self._products:
            yield p


class _FakeService:
    def __init__(self, products: list[dict]) -> None:
        self.client = _FakeClient(products)


def _raw(handle: str, **extra) -> dict:
    """Forma real de la Admin API (subset) con las medidas que se pasen."""
    return {
        "id": f"prod_{handle}",
        "title": handle.title(),
        "handle": handle,
        "status": "published",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "options": [],
        "variants": [
            {
                "id": f"v_{handle}",
                "title": "Unico",
                "options": [],
                "prices": [{"id": "pr", "amount": 16000, "currency_code": "cop"}],
            }
        ],
        "images": [],
        "tags": [],
        "categories": [],
        **extra,
    }


async def _pull(raw: dict) -> dict:
    uc = PullCatalogUseCase(_FakeService([raw]))  # type: ignore[arg-type]
    result = await uc.execute(CatalogSyncInput())
    return json.loads(result.products_json)[0]


@pytest.mark.asyncio
async def test_pull_keeps_product_level_dimensions():
    product = await _pull(_raw("calabaza", height=9, width=6))

    assert product.get("dimensions") == {
        "height_cm": 9.0,
        "width_cm": 6.0,
        "length_cm": None,
        "weight_g": None,
    }


@pytest.mark.asyncio
async def test_pull_zero_dimensions_mean_no_data():
    """La Trilogía del Terror tiene 0×0 en Medusa: eso NO es una medida."""
    product = await _pull(_raw("trilogia-del-terror", height=0, width=0))

    assert product.get("dimensions") is None


@pytest.mark.asyncio
async def test_pull_without_dimensions_stays_none():
    product = await _pull(_raw("momia"))

    assert product.get("dimensions") is None


@pytest.mark.asyncio
async def test_snapshot_read_path_keeps_dimensions():
    """El snapshot que lee el agente pasa por el parser canónico
    `product_dto_from_raw` (lección #178: un parser que no mapea un campo
    lo borra en silencio aunque el pull lo traiga)."""
    from src.platform.catalog.dtos import CatalogDimensionsDTO, product_dto_from_raw

    raw = await _pull(_raw("calabaza", height=9, width=6))

    assert product_dto_from_raw(raw).dimensions == CatalogDimensionsDTO(
        height_cm=9.0, width_cm=6.0
    )


def test_snapshot_written_before_dimensions_still_parses():
    from src.platform.catalog.dtos import product_dto_from_raw

    old = {"id": "prod_x", "handle": "x", "title": "X", "variants": []}

    assert product_dto_from_raw(old).dimensions is None


@pytest.mark.asyncio
async def test_pull_falls_back_to_first_variant_with_dimensions():
    raw = _raw("velon")
    raw["variants"] = [
        {**raw["variants"][0], "id": "v_sin"},
        {**raw["variants"][0], "id": "v_con", "height": 10, "width": 7},
    ]

    product = await _pull(raw)

    assert product.get("dimensions") == {
        "height_cm": 10.0,
        "width_cm": 7.0,
        "length_cm": None,
        "weight_g": None,
    }
