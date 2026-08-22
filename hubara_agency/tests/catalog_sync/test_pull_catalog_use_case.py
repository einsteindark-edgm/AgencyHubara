"""PullCatalogUseCase — mapeo Medusa→DTO con Decimal→str."""
from __future__ import annotations

import json

import pytest

from src.plugins.catalog.agent.contracts import CatalogSyncInput
from src.plugins.catalog.agent.use_cases.pull_catalog import PullCatalogUseCase


class _FakeClient:
    """Minimal stub para iter_products."""

    def __init__(self, products: list[dict]) -> None:
        self._products = products

    async def iter_products(self, **kwargs):
        for p in self._products:
            yield p


class _FakeService:
    def __init__(self, products: list[dict]) -> None:
        self.client = _FakeClient(products)


def _collection(handle: str, title: str) -> dict:
    return {"id": f"pcol_{handle}", "title": title, "handle": handle}


def _product(
    pid: str, handle: str, title: str, *, collection: dict | None = None
) -> dict:
    raw = {
        "id": pid,
        "title": title,
        "handle": handle,
        "status": "published",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "variants": [],
        "images": [],
    }
    if collection is not None:
        raw["collection"] = collection
    return raw



@pytest.mark.asyncio
async def test_pull_one_product_with_decimal_as_str():
    svc = _FakeService(
        [
            {
                "id": "p1",
                "title": "Vela Lavanda",
                "handle": "vela-lavanda",
                "status": "published",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "variants": [
                    {
                        "id": "v1",
                        "title": "u",
                        "prices": [
                            {
                                "id": "pr1",
                                "amount": 49.99,
                                "currency_code": "usd",
                            }
                        ],
                    }
                ],
                "images": [],
                "tags": [],
                "categories": [],
            }
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())

    assert result.count == 1
    payload = json.loads(result.products_json)
    assert payload[0]["handle"] == "vela-lavanda"
    # Critical: Decimal preservada como string (R-JSON-safe)
    assert payload[0]["variants"][0]["prices"][0]["amount"] == "49.99"
    assert isinstance(payload[0]["variants"][0]["prices"][0]["amount"], str)


@pytest.mark.asyncio
async def test_pull_empty_catalog():
    svc = _FakeService([])
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())
    assert result.count == 0
    assert json.loads(result.products_json) == []


@pytest.mark.asyncio
async def test_products_in_allowed_collections_are_included():
    """`home_banner` y `home_new_arrivals` son merchandising, no data sucia.

    Los productos destacados en el home del storefront son productos reales y
    vendibles — aparecer en la vitrina NO puede sacarlos del catálogo
    conversacional. Guard del incidente 2026-08-21: `Duo Zodiacal`, `Ángel`,
    `Velón Cisne`, `Velón Pinguino`... quedaban invisibles para el agente.
    """
    svc = _FakeService(
        [
            _product("p1", "vela-lavanda", "Vela Lavanda"),
            _product(
                "p2",
                "duo-zodiacal",
                "Duo Zodiacal",
                collection=_collection("home_new_arrivals", "Los más vendidos"),
            ),
            _product(
                "p3",
                "velon-pinguino",
                "Velón Pinguino",
                collection=_collection("home_banner", "Home Banner"),
            ),
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())

    assert result.count == 3
    handles = {p["handle"] for p in json.loads(result.products_json)}
    assert handles == {"vela-lavanda", "duo-zodiacal", "velon-pinguino"}


@pytest.mark.asyncio
async def test_products_in_other_collections_stay_filtered_out():
    """El resto de las collections sigue fuera del catálogo conversacional.

    `Story showcase` y `Promociones` agrupan contenido del storefront (bloques
    del home, promos armadas) que no son productos que el agente deba vender.
    El allowlist es explícito: collection desconocida → fuera.
    """
    svc = _FakeService(
        [
            _product("p1", "vela-lavanda", "Vela Lavanda"),
            _product(
                "p2",
                "vela-natural",
                "Vela Natural",
                collection=_collection("home_showcase", "Story showcase"),
            ),
            _product(
                "p3",
                "promocion-religiosa",
                "Promocion Religiosa",
                collection=_collection("products_promociones", "Promociones"),
            ),
            _product(
                "p4",
                "angel",
                "Ángel",
                collection=_collection("home_new_arrivals", "Los más vendidos"),
            ),
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())

    assert result.count == 2
    handles = {p["handle"] for p in json.loads(result.products_json)}
    assert handles == {"vela-lavanda", "angel"}


@pytest.mark.asyncio
async def test_allowed_collections_are_injectable():
    """El allowlist es un parámetro — el operador puede sumar collections."""
    svc = _FakeService(
        [
            _product(
                "p1",
                "promocion-religiosa",
                "Promocion Religiosa",
                collection=_collection("products_promociones", "Promociones"),
            ),
        ]
    )
    use_case = PullCatalogUseCase(
        medusa_service=svc,
        allowed_collection_handles={"products_promociones"},
    )
    result = await use_case.execute(CatalogSyncInput())
    assert result.count == 1


@pytest.mark.asyncio
async def test_tags_and_categories_flattened():
    svc = _FakeService(
        [
            {
                "id": "p1",
                "title": "X",
                "handle": "x",
                "status": "published",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "tags": [
                    {"id": "t1", "value": "Aroma: Lavanda"},
                    {"id": "t2", "value": "Color: Morado"},
                ],
                "categories": [
                    {"id": "c1", "name": "Velas", "handle": "velas"},
                    {"id": "c2", "name": "Aromaticas"},  # sin handle
                ],
                "variants": [],
                "images": [],
            }
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())
    payload = json.loads(result.products_json)
    assert payload[0]["tags"] == ["Aroma: Lavanda", "Color: Morado"]
    # Categoria con handle usa handle; sin handle cae a name
    assert payload[0]["categories"] == ["velas", "Aromaticas"]
