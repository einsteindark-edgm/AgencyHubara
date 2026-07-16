"""Read-path del snapshot con options reales (caso Duo Zodiacal, 2026-07-15).

El snapshot escrito por el pull ahora incluye `options` a nivel producto y
por variante. El read-path debe reconstruirlos — y seguir leyendo snapshots
VIEJOS (sin la key) sin romper (options → None).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


def _write_snapshot(tmp_path: Path, products: list[dict]) -> Path:
    (tmp_path / "snapshot.json").write_text(json.dumps(products))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": len(products),
            }
        )
    )
    return tmp_path


@pytest.mark.asyncio
async def test_get_by_handle_parses_options(tmp_path: Path):
    snap_dir = _write_snapshot(
        tmp_path,
        [
            {
                "id": "prod_duo",
                "handle": "duo-zodiacal",
                "title": "Duo Zodiacal",
                "status": "published",
                "options": {"Signo": ["Aries", "Leo"]},
                "variants": [
                    {
                        "id": "v_leo",
                        "title": "Leo",
                        "options": {"Signo": "Leo"},
                        "prices": [
                            {"amount": "35000", "currency_code": "cop"}
                        ],
                    }
                ],
            }
        ],
    )
    client = LocalSnapshotCatalogClient(snap_dir)
    product = await client.get_by_handle("duo-zodiacal")
    assert product.options == {"Signo": ["Aries", "Leo"]}
    assert product.variants[0].options == {"Signo": "Leo"}


@pytest.mark.asyncio
async def test_get_by_handle_old_snapshot_without_options(tmp_path: Path):
    """Snapshot pre-options en disco → el read-path NO explota."""
    snap_dir = _write_snapshot(
        tmp_path,
        [
            {
                "id": "1",
                "handle": "luz-serena",
                "title": "Luz Serena",
                "status": "published",
                "variants": [
                    {
                        "id": "v1",
                        "title": "u",
                        "prices": [
                            {"amount": "23000", "currency_code": "cop"}
                        ],
                    }
                ],
            }
        ],
    )
    client = LocalSnapshotCatalogClient(snap_dir)
    product = await client.get_by_handle("luz-serena")
    assert product.options is None
    assert product.variants[0].options is None
