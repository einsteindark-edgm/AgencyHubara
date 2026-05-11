"""DEFAULT_PRODUCT_FIELDS incluye `*variants` Y `*variants.prices` por separado.

Gotcha §4.1 de MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md:
  `*variants` NO incluye automaticamente `*variants.prices`.
"""
from __future__ import annotations

from src.platform.medusa.client import DEFAULT_PRODUCT_FIELDS


def test_contains_both_variants_and_variants_prices():
    fields = DEFAULT_PRODUCT_FIELDS.split(",")
    assert "*variants" in fields
    assert "*variants.prices" in fields


def test_contains_scalar_metadata_and_thumbnail():
    fields = DEFAULT_PRODUCT_FIELDS.split(",")
    # metadata y thumbnail son escalares (sin *)
    assert "metadata" in fields
    assert "thumbnail" in fields
    assert "*metadata" not in fields
    assert "*thumbnail" not in fields
