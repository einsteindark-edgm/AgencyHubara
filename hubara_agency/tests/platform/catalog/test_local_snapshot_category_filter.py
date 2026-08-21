"""Filtro por categoría en el snapshot: determinista y sin contaminación.

Antes, "muéstrame las religiosas" era un substring search que también pegaba
contra description → traía productos de otra categoría. Con `category=` el
filtro es de pertenencia real (product.categories), no de texto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.fixture
def snap_dir(tmp_path: Path) -> Path:
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "corona",
                    "title": "Corona de Redención",
                    "status": "published",
                    "categories": ["velas-religiosas"],
                    "category_labels": {"velas-religiosas": "Velas Religiosas"},
                },
                {
                    "id": "2",
                    "handle": "cruz-de-vida",
                    "title": "Cruz de Vida",
                    "status": "published",
                    "categories": ["velas-religiosas"],
                    "category_labels": {"velas-religiosas": "Velas Religiosas"},
                },
                {
                    "id": "3",
                    "handle": "luz-serena",
                    "title": "Luz Serena",
                    "status": "published",
                    # Menciona "religiosas" en la description: el search de
                    # texto la traía como falso positivo.
                    "description": "ideal para fechas religiosas y familiares",
                    "categories": ["velas-aromaticas"],
                    "category_labels": {"velas-aromaticas": "Velas Aromáticas"},
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 3,
            }
        )
    )
    return tmp_path


@pytest.mark.asyncio
async def test_list_categories_returns_closed_list_with_counts(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    cats = await client.list_categories()
    assert [(c.slug, c.label, c.product_count) for c in cats] == [
        ("velas-aromaticas", "Velas Aromáticas", 1),
        ("velas-religiosas", "Velas Religiosas", 2),
    ]


@pytest.mark.asyncio
async def test_category_filter_returns_only_members(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    result = await client.search(q="", category="religiosas")
    assert [p.handle for p in result.results] == ["corona", "cruz-de-vida"]
    assert result.category is not None
    assert result.category.matched is not None
    assert result.category.matched.slug == "velas-religiosas"


@pytest.mark.asyncio
async def test_category_filter_tolerates_typo(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    result = await client.search(q="", category="velas religosas")
    assert [p.handle for p in result.results] == ["corona", "cruz-de-vida"]


@pytest.mark.asyncio
async def test_unknown_category_returns_no_products_not_a_wrong_set(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    result = await client.search(q="", category="zapatos")
    assert result.results == []
    assert result.count == 0
    assert result.category is not None
    assert result.category.confidence == "none"


@pytest.mark.asyncio
async def test_query_filters_within_the_category(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    result = await client.search(q="corona", category="religiosas")
    assert [p.handle for p in result.results] == ["corona"]


@pytest.mark.asyncio
async def test_catalog_without_categories_degrades_to_text_search(tmp_path: Path):
    """Si el operador no cargó categorías en Medusa, `category=` no puede
    dejar al cliente sin respuesta: cae a búsqueda de texto con ese término."""
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "corona",
                    "title": "Corona Religiosa",
                    "status": "published",
                },
                {
                    "id": "2",
                    "handle": "luz-serena",
                    "title": "Luz Serena",
                    "status": "published",
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 2,
            }
        )
    )
    client = LocalSnapshotCatalogClient(tmp_path)
    result = await client.search(q="", category="religiosa")
    assert [p.handle for p in result.results] == ["corona"]
    assert result.category is not None
    assert result.category.confidence == "no_categories"
