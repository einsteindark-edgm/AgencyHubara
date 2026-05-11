"""mtime change → automatic reload sin restart."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.mark.asyncio
async def test_mtime_change_triggers_reload(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "h1",
                    "title": "T1",
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
    client = LocalSnapshotCatalogClient(tmp_path)

    res1 = await client.search(q="h1")
    assert res1.count == 1

    # Atomic write con mtime explicitamente nuevo (POSIX stat granular puede ser segundos).
    new_payload = json.dumps(
        [
            {"id": "1", "handle": "h1", "title": "T1", "status": "published"},
            {"id": "2", "handle": "h2", "title": "T2", "status": "published"},
        ]
    )
    tmp = tmp_path / "snapshot.json.new"
    tmp.write_text(new_payload)
    future = time.time() + 5
    os.utime(tmp, (future, future))
    os.replace(tmp, tmp_path / "snapshot.json")

    res2 = await client.search(q="h")
    assert res2.count == 2
