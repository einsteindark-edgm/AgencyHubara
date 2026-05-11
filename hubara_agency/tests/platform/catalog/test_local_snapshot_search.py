"""search() — substring case-insensitive, truncation, manifest stale check."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.fixture
def snap_dir(tmp_path: Path) -> Path:
    snap = tmp_path / "snapshot.json"
    snap.write_text(
        json.dumps(
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
                },
                {
                    "id": "2",
                    "handle": "vela-cruz",
                    "title": "Cruz de Vida",
                    "status": "published",
                    "variants": [
                        {
                            "id": "v2",
                            "title": "u",
                            "prices": [
                                {"amount": "17000", "currency_code": "cop"}
                            ],
                        }
                    ],
                },
                {
                    "id": "3",
                    "handle": "luz-belen",
                    "title": "Luz de Belén",
                    "status": "published",
                    "variants": [
                        {
                            "id": "v3",
                            "title": "u",
                            "prices": [
                                {"amount": "20000", "currency_code": "cop"}
                            ],
                        }
                    ],
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
async def test_search_substring_case_insensitive(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="LUZ", limit=10)
    assert res.count == 2
    handles = {p.handle for p in res.results}
    assert handles == {"luz-serena", "luz-belen"}
    assert res.stale is False


@pytest.mark.asyncio
async def test_search_matches_handle_too(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="cruz", limit=10)
    assert res.count == 1
    assert res.results[0].handle == "vela-cruz"


@pytest.mark.asyncio
async def test_search_truncates_when_over_limit(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="luz", limit=1)
    assert res.count == 2
    assert len(res.results) == 1
    assert res.truncated is True


@pytest.mark.asyncio
async def test_search_zero_matches(snap_dir: Path):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="patata-frita")
    assert res.count == 0
    assert res.results == []
    assert res.truncated is False
