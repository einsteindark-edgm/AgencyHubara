"""WriteSnapshotUseCase — atomic write, by_handle/, manifest."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.catalog_sync.contracts import WriteSnapshotInput
from src.catalog_sync.use_cases.write_snapshot import WriteSnapshotUseCase


@pytest.mark.asyncio
async def test_write_creates_snapshot_by_handle_and_manifest(tmp_path: Path):
    products = [
        {
            "id": "1",
            "handle": "luz-serena",
            "title": "Luz Serena",
            "status": "published",
        },
        {
            "id": "2",
            "handle": "vela-cruz",
            "title": "Cruz",
            "status": "published",
        },
    ]
    use_case = WriteSnapshotUseCase()
    result = await use_case.execute(
        WriteSnapshotInput(
            products_json=json.dumps(products),
            count=2,
            fetched_at="2026-05-07T12:00:00+00:00",
            snapshot_dir=str(tmp_path),
        )
    )

    assert (tmp_path / "snapshot.json").exists()
    assert (tmp_path / "by_handle" / "luz-serena.json").exists()
    assert (tmp_path / "by_handle" / "vela-cruz.json").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["product_count"] == 2
    assert manifest["fetched_at"] == "2026-05-07T12:00:00+00:00"
    assert isinstance(manifest["version"], str)
    # 1 snapshot + 2 by_handle + 1 manifest = 4
    assert result.files_written == 4
    assert result.bytes_written > 0


@pytest.mark.asyncio
async def test_write_cleans_orphan_handles(tmp_path: Path):
    by_handle = tmp_path / "by_handle"
    by_handle.mkdir()
    (by_handle / "old-product.json").write_text("{}")

    use_case = WriteSnapshotUseCase()
    await use_case.execute(
        WriteSnapshotInput(
            products_json=json.dumps(
                [
                    {
                        "id": "1",
                        "handle": "new-product",
                        "title": "X",
                        "status": "published",
                    }
                ]
            ),
            count=1,
            fetched_at="2026-01-01T00:00:00Z",
            snapshot_dir=str(tmp_path),
        )
    )

    # cleaned
    assert not (by_handle / "old-product.json").exists()
    assert (by_handle / "new-product.json").exists()


@pytest.mark.asyncio
async def test_no_tmp_files_left_behind(tmp_path: Path):
    use_case = WriteSnapshotUseCase()
    await use_case.execute(
        WriteSnapshotInput(
            products_json=json.dumps(
                [
                    {
                        "id": "1",
                        "handle": "h",
                        "title": "T",
                        "status": "published",
                    }
                ]
            ),
            count=1,
            fetched_at="2026-01-01T00:00:00Z",
            snapshot_dir=str(tmp_path),
        )
    )
    # Ningun archivo .tmp residual
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == []
