"""DTOs JSON-roundtrip."""
from __future__ import annotations

import json
from dataclasses import asdict

from src.catalog_sync.contracts import (
    CatalogSyncInput,
    PullCatalogResult,
    WriteSnapshotInput,
    WriteSnapshotResult,
)


def test_all_dtos_json_roundtrip():
    dtos = [
        CatalogSyncInput(),
        PullCatalogResult(
            products_json="[]", count=0, fetched_at="2026-01-01T00:00:00Z"
        ),
        WriteSnapshotInput(
            products_json="[]",
            count=0,
            fetched_at="2026-01-01T00:00:00Z",
            snapshot_dir="/tmp/x",
        ),
        WriteSnapshotResult(version="abc", bytes_written=10, files_written=1),
    ]
    for dto in dtos:
        s = json.dumps(asdict(dto))
        back = json.loads(s)
        assert isinstance(back, dict)


def test_catalog_sync_input_defaults():
    inp = CatalogSyncInput()
    assert inp.tenant_id == "default"
    assert inp.force_full_refresh is True
    assert inp.snapshot_dir == ""
