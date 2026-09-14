"""get_by_sku() — the storefront's `ref: HUB-…` resolves against the snapshot."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.catalog.errors import ProductNotFoundError
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.fixture
def snap_dir(tmp_path: Path) -> Path:
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published",
                    "variants": [{"id": "v1", "title": "Unico", "sku": "HUB-SERENA"}],
                },
                {
                    "id": "2", "handle": "duo-zodiacal", "title": "Duo Zodiacal", "status": "published",
                    "variants": [
                        {"id": "v_leo", "title": "Leo", "sku": "HUB-DUOZOD-LEO"},
                        {"id": "v_esc", "title": "Escorpio", "sku": "HUB-DUOZOD-ESCORPIO"},
                    ],
                },
            ]
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 2})
    )
    return tmp_path


@pytest.mark.asyncio
async def test_finds_the_product_that_owns_the_variant_sku(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    assert (await client.get_by_sku("HUB-DUOZOD-ESCORPIO")).handle == "duo-zodiacal"
    assert (await client.get_by_sku("HUB-SERENA")).handle == "luz-serena"


@pytest.mark.asyncio
async def test_unknown_sku_raises_product_not_found(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    with pytest.raises(ProductNotFoundError):
        await client.get_by_sku("HUB-NOPE")
