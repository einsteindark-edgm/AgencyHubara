"""get_by_handle() — fast path (by_handle/), slow path (snapshot), 404."""
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
                    "id": "1",
                    "handle": "luz-serena",
                    "title": "Luz Serena",
                    "status": "published",
                }
            ]
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 1,
            }
        )
    )
    return tmp_path


@pytest.mark.asyncio
async def test_fast_path_via_by_handle_dir(tmp_path: Path):
    by_handle = tmp_path / "by_handle"
    by_handle.mkdir()
    (by_handle / "luz-serena.json").write_text(
        json.dumps(
            {
                "id": "1",
                "handle": "luz-serena",
                "title": "Luz Serena",
                "status": "published",
            }
        )
    )
    client = LocalSnapshotCatalogClient(tmp_path)
    p = await client.get_by_handle("luz-serena")
    assert p.handle == "luz-serena"
    assert p.title == "Luz Serena"


@pytest.mark.asyncio
async def test_slow_path_falls_back_to_snapshot(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    p = await client.get_by_handle("luz-serena")
    assert p.title == "Luz Serena"


@pytest.mark.asyncio
async def test_unknown_handle_raises_not_found(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    with pytest.raises(ProductNotFoundError) as exc:
        await client.get_by_handle("inventado")
    assert exc.value.handle == "inventado"
