"""Pull de productos con options reales (caso Duo Zodiacal, 2026-07-15).

Un producto Medusa con option "Signo" y una variante por valor debe llegar
al snapshot con la estructura de options intacta:

  * producto.options == {"Signo": ["Aries", "Leo", ...]}
  * variante.options == {"Signo": "Aries"}

Sin esto el agente ve 12 variantes con título pero no sabe cuál es el eje
de selección — y el mapper de Meta no puede emitir items per-variante.
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


def _duo_zodiacal_raw() -> dict:
    """Forma real de la Admin API (subset): option Signo + 2 variantes."""
    return {
        "id": "prod_duo",
        "title": "Duo Zodiacal",
        "handle": "duo-zodiacal",
        "status": "published",
        "created_at": "2026-07-16T01:37:14Z",
        "updated_at": "2026-07-16T01:37:14Z",
        "options": [
            {
                "id": "opt_signo",
                "title": "Signo",
                "values": [
                    {"id": "optval_aries", "value": "Aries"},
                    {"id": "optval_leo", "value": "Leo"},
                ],
            }
        ],
        "variants": [
            {
                "id": "v_aries",
                "title": "Aries",
                "options": [{"id": "optval_aries", "value": "Aries"}],
                "prices": [
                    {"id": "pr1", "amount": 35000, "currency_code": "cop"}
                ],
            },
            {
                "id": "v_leo",
                "title": "Leo",
                "options": [{"id": "optval_leo", "value": "Leo"}],
                "prices": [
                    {"id": "pr2", "amount": 35000, "currency_code": "cop"}
                ],
            },
        ],
        "images": [],
        "tags": [],
        "categories": [],
    }


@pytest.mark.asyncio
async def test_pull_preserves_product_options():
    svc = _FakeService([_duo_zodiacal_raw()])
    result = await PullCatalogUseCase(medusa_service=svc).execute(
        CatalogSyncInput()
    )
    payload = json.loads(result.products_json)
    assert payload[0]["options"] == {"Signo": ["Aries", "Leo"]}


@pytest.mark.asyncio
async def test_pull_preserves_variant_option_values():
    svc = _FakeService([_duo_zodiacal_raw()])
    result = await PullCatalogUseCase(medusa_service=svc).execute(
        CatalogSyncInput()
    )
    payload = json.loads(result.products_json)
    variants = {v["title"]: v for v in payload[0]["variants"]}
    assert variants["Aries"]["options"] == {"Signo": "Aries"}
    assert variants["Leo"]["options"] == {"Signo": "Leo"}


@pytest.mark.asyncio
async def test_pull_product_without_options_keeps_none():
    """Backward-compat: producto legacy (tags, 1 variante) → options None."""
    raw = _duo_zodiacal_raw()
    raw.pop("options")
    raw["variants"] = [
        {
            "id": "v1",
            "title": "Unico",
            "prices": [{"id": "pr1", "amount": 19000, "currency_code": "cop"}],
        }
    ]
    svc = _FakeService([raw])
    result = await PullCatalogUseCase(medusa_service=svc).execute(
        CatalogSyncInput()
    )
    payload = json.loads(result.products_json)
    assert payload[0]["options"] is None
    assert payload[0]["variants"][0]["options"] is None
