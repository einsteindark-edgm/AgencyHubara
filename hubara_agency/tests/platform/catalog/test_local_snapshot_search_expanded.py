"""Post-fix #1+#7: search amplia a tags, categories, description + q="" lista todo."""
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
                # Title "Corona" sin "vela" en el nombre — pero "velas" en description
                {
                    "id": "1",
                    "handle": "corona",
                    "title": "Corona de Redención",
                    "status": "published",
                    "description": "velitas para quemar en semana santa",
                    "tags": ["Aroma: Caballero", "Color: Blanco"],
                    "categories": ["religiosas"],
                },
                # Title plano sin tags útiles
                {
                    "id": "2",
                    "handle": "luz-serena",
                    "title": "Luz Serena",
                    "status": "published",
                    "tags": ["Aroma: Lavanda"],
                },
                # Producto fuera de tema
                {
                    "id": "3",
                    "handle": "otro",
                    "title": "Otro Producto",
                    "status": "published",
                    "categories": ["otros"],
                },
            ]
        )
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
async def test_empty_query_returns_all_products(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="", limit=10)
    assert res.count == 3
    assert {p.handle for p in res.results} == {"corona", "luz-serena", "otro"}


@pytest.mark.asyncio
async def test_search_matches_tag(snap_dir: Path):
    """Cliente: 'algo de lavanda' → matchea tag 'Aroma: Lavanda'."""
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="lavanda")
    assert res.count == 1
    assert res.results[0].handle == "luz-serena"


@pytest.mark.asyncio
async def test_search_matches_category(snap_dir: Path):
    """Cliente: 'velas religiosas' → matchea category 'religiosas'."""
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="religiosa")
    assert res.count == 1
    assert res.results[0].handle == "corona"


@pytest.mark.asyncio
async def test_search_matches_description(snap_dir: Path):
    """Cliente: '¿qué velas tienen?' — la palabra 'velitas' está en description.

    Este es el caso real del bug #1 detectado en wa_573125671604.
    """
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="velitas")
    assert res.count == 1
    assert res.results[0].handle == "corona"


@pytest.mark.asyncio
async def test_search_is_strict_substring_not_stemming(snap_dir: Path):
    """Documenta la limitacion real del matcher: substring exacto, NO stemming.

    `'velitas'` NO matchea `q='vela'` (los chars son v-e-l-i-t-a-s, sin
    'vela' consecutivo). Esto explica por que en el bug wa_573125671604 el
    LLM tuvo que hacer 4 busquedas para encontrar el catalogo. La solucion
    contractual es que el LLM use `q=""` para listar todo cuando la query
    es generica.
    """
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="vela")
    assert res.count == 0  # ningun campo contiene "vela" como substring exacto

    # Pero substring valida (prefijo de "velitas") si funciona:
    res = await client.search(q="velita")
    assert res.count == 1
    assert res.results[0].handle == "corona"
