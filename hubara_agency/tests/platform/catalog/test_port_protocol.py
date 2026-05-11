"""CatalogPort runtime_checkable: LocalSnapshotCatalogClient lo satisface."""
from __future__ import annotations

from pathlib import Path

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.port import CatalogPort


def test_local_client_satisfies_port_structurally(tmp_path: Path):
    client = LocalSnapshotCatalogClient(tmp_path)
    assert isinstance(client, CatalogPort)
