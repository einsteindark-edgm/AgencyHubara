"""DTOs JSON-safe — roundtrip via dataclasses.asdict + json.dumps/loads."""
from __future__ import annotations

import json
from dataclasses import asdict

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)


def test_product_dto_roundtrips_through_json():
    p = CatalogProductDTO(
        id="prod_1",
        handle="x",
        title="Foo",
        status="published",
        variants=[
            CatalogVariantDTO(
                id="v1",
                title="u",
                prices=[CatalogPriceDTO(amount="49.99", currency_code="usd")],
            )
        ],
        images=[CatalogImageDTO(url="http://x", rank=0)],
        tags=["A", "B"],
        metadata={"key": "value"},
    )
    s = json.dumps(asdict(p))
    back = json.loads(s)
    assert back["handle"] == "x"
    assert back["variants"][0]["prices"][0]["amount"] == "49.99"
    assert back["tags"] == ["A", "B"]
    assert back["metadata"] == {"key": "value"}
