"""Closed-list de categorías derivada del snapshot."""
from __future__ import annotations

from src.platform.catalog.categories import collect_categories
from src.platform.catalog.dtos import CatalogProductDTO


def _product(handle: str, categories: list[str], labels=None) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=handle.title(),
        status="published",
        categories=categories,
        category_labels=labels,
    )


def test_collect_counts_products_per_category_with_real_labels():
    products = [
        _product("a", ["velas-religiosas"], {"velas-religiosas": "Velas Religiosas"}),
        _product("b", ["velas-religiosas"], {"velas-religiosas": "Velas Religiosas"}),
        _product("c", ["difusores"], {"difusores": "Difusores"}),
    ]
    cats = collect_categories(products)
    assert [(c.slug, c.label, c.product_count) for c in cats] == [
        ("difusores", "Difusores", 1),
        ("velas-religiosas", "Velas Religiosas", 2),
    ]


def test_collect_falls_back_to_deslugified_label_on_old_snapshots():
    cats = collect_categories([_product("a", ["velas-religiosas"])])
    assert cats[0].label == "Velas Religiosas"
