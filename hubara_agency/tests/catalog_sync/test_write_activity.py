"""write_snapshot_activity — ActivityEnvironment + tmp_path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.catalog_sync.activities.write import write_snapshot_activity
from src.catalog_sync.contracts import WriteSnapshotInput


@pytest.mark.asyncio
async def test_write_activity_creates_files(tmp_path: Path):
    env = ActivityEnvironment()
    result = await env.run(
        write_snapshot_activity,
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
        ),
    )
    assert result.files_written == 3  # snapshot + 1 by_handle + manifest
    assert (tmp_path / "snapshot.json").exists()
    assert (tmp_path / "by_handle" / "h.json").exists()
    assert (tmp_path / "manifest.json").exists()


@pytest.mark.asyncio
async def test_write_activity_falls_back_to_env_when_dir_empty(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(tmp_path))
    # Importante: importar el modulo paths fresco para que pick up el env override
    env = ActivityEnvironment()
    result = await env.run(
        write_snapshot_activity,
        WriteSnapshotInput(
            products_json=json.dumps([]),
            count=0,
            fetched_at="2026-01-01T00:00:00Z",
            snapshot_dir="",  # empty → activity resolves via env
        ),
    )
    # files_written = snapshot + manifest = 2 (no by_handle children)
    assert result.files_written == 2
    assert (tmp_path / "snapshot.json").exists()
