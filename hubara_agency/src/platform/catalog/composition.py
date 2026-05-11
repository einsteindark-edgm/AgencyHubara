"""DI factory para el CatalogPort.

Devuelve por default un LocalSnapshotCatalogClient apuntando al
`get_snapshot_dir()`. Singleton por proceso (lru_cache(1)) — la cache
mtime-aware vive en la instancia, asi que compartir es correcto.
"""
from __future__ import annotations

from functools import lru_cache

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir
from src.platform.catalog.port import CatalogPort


@lru_cache(maxsize=1)
def get_catalog_client() -> CatalogPort:
    return LocalSnapshotCatalogClient(
        snapshot_dir=get_snapshot_dir(),
        max_age_minutes=get_max_age_minutes(),
    )
