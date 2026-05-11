"""Failure modes — missing/corrupt snapshot levanta CatalogUnavailableError."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.catalog.errors import CatalogUnavailableError
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.mark.asyncio
async def test_no_snapshot_raises(tmp_path: Path):
    client = LocalSnapshotCatalogClient(tmp_path)
    with pytest.raises(CatalogUnavailableError):
        await client.search(q="x")


@pytest.mark.asyncio
async def test_corrupt_snapshot_raises(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text("not valid json {{{")
    client = LocalSnapshotCatalogClient(tmp_path)
    with pytest.raises(CatalogUnavailableError):
        await client.search(q="x")
