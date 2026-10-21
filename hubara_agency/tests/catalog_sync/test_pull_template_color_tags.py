"""Las etiquetas «Color:» que contradicen la opción «Color» no llegan al snapshot.

Caso 2026-10-17/18: las dos cajas de velas de deseos tenían en Medusa la
opción real «Color» (Rojo y verde, Multicolor) y además 11 etiquetas «Color:»
copiadas de la plantilla de los velones. El bot leía las etiquetas: rechazó
«Multicolor» en 4 conversaciones y le mostró a un cliente colores que la caja
no tiene. La variante real es la opción; un producto sin opción de color
(los velones) conserva sus etiquetas.
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


def _raw(handle: str, options: list[dict], tags: list[str]) -> dict:
    return {
        "id": f"prod_{handle}",
        "title": handle,
        "handle": handle,
        "status": "published",
        "created_at": "2026-10-16T16:18:24Z",
        "updated_at": "2026-10-16T16:18:24Z",
        "options": options,
        "variants": [],
        "images": [],
        "tags": [{"id": f"tag_{i}", "value": t} for i, t in enumerate(tags)],
        "categories": [],
    }


COLOR_OPTION = [
    {
        "id": "opt_color",
        "title": "Color",
        "values": [
            {"id": "optval_ryv", "value": "Rojo y verde"},
            {"id": "optval_multi", "value": "Multicolor"},
        ],
    }
]
UNICO = [{"id": "opt_unico", "title": "Unico", "values": [{"id": "optval_unico", "value": "Unico"}]}]
TEMPLATE = ["Aroma: Navidad", "Color: Blanco", "Color: Azul", "Color: multicolor"]


async def _tags(*raws: dict) -> dict[str, list[str]]:
    result = await PullCatalogUseCase(_FakeService(list(raws))).execute(CatalogSyncInput())
    return {p["handle"]: p["tags"] for p in json.loads(result.products_json)}


@pytest.mark.asyncio
async def test_color_tags_that_the_color_option_does_not_have_are_dropped():
    tags = await _tags(_raw("velas-de-deseos-cuadradas", COLOR_OPTION, TEMPLATE))

    assert tags["velas-de-deseos-cuadradas"] == ["Aroma: Navidad", "Color: multicolor"]


@pytest.mark.asyncio
async def test_a_product_without_a_color_option_keeps_its_color_tags():
    tags = await _tags(_raw("velon-cisne", UNICO, TEMPLATE))

    assert tags["velon-cisne"] == TEMPLATE
