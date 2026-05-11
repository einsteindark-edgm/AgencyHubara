"""SearchResult.stale = True cuando manifest.fetched_at > max_age_minutes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.mark.asyncio
async def test_stale_when_manifest_old(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "h",
                    "title": "T",
                    "status": "published",
                }
            ]
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v",
                "fetched_at": "2020-01-01T00:00:00+00:00",
                "product_count": 1,
            }
        )
    )
    client = LocalSnapshotCatalogClient(tmp_path, max_age_minutes=30)
    res = await client.search(q="h")
    assert res.stale is True


@pytest.mark.asyncio
async def test_fresh_when_manifest_recent(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "h",
                    "title": "T",
                    "status": "published",
                }
            ]
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "product_count": 1,
            }
        )
    )
    client = LocalSnapshotCatalogClient(tmp_path, max_age_minutes=30)
    res = await client.search(q="h")
    assert res.stale is False
