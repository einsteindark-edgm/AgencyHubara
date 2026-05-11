"""DI factories del catalog_sync agent.

`lru_cache(1)` para reusar `MedusaProductService` (recurso de larga vida)
a traves de invocaciones de activity en el mismo proceso. R-STATELESS se
respeta porque la activity rebuilds via factory (no cache module-level
fuera de lru_cache, que es metadata de registracion).
"""
from __future__ import annotations

from functools import lru_cache

from src.catalog_sync.use_cases.pull_catalog import PullCatalogUseCase
from src.catalog_sync.use_cases.write_snapshot import WriteSnapshotUseCase
from src.platform.medusa.composition import get_medusa_product_service


@lru_cache(maxsize=1)
def get_pull_catalog_use_case() -> PullCatalogUseCase:
    return PullCatalogUseCase(medusa_service=get_medusa_product_service())


@lru_cache(maxsize=1)
def get_write_snapshot_use_case() -> WriteSnapshotUseCase:
    return WriteSnapshotUseCase()
