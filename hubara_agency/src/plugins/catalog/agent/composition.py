"""DI factories del catalog_sync agent.

`lru_cache(1)` para reusar `MedusaProductService` (recurso de larga vida)
a traves de invocaciones de activity en el mismo proceso. R-STATELESS se
respeta porque la activity rebuilds via factory (no cache module-level
fuera de lru_cache, que es metadata de registracion).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache

from src.platform.medusa.composition import get_medusa_product_service
from src.platform.meta_catalog.client import MetaCatalogClient
from src.platform.meta_catalog.port import MetaCatalogPort
from src.plugins.catalog.agent.use_cases.pull_catalog import PullCatalogUseCase
from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
    PushMetaCatalogUseCase,
)
from src.plugins.catalog.agent.use_cases.write_snapshot import WriteSnapshotUseCase


def allowed_collection_handles(env: Mapping[str, str]) -> frozenset[str] | None:
    """Las colecciones de Medusa cuyos productos entran al catálogo: las de la
    TIENDA (`CATALOG_COLLECTION_HANDLES`, separadas por coma, nace en
    Terraform `tenants.<t>.store`). Sin variable, None = el default del caso
    de uso (las vitrinas de la tienda madre); vacía a propósito = solo los
    productos sin colección."""
    raw = env.get("CATALOG_COLLECTION_HANDLES")
    if raw is None:
        return None
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


@lru_cache(maxsize=1)
def get_pull_catalog_use_case() -> PullCatalogUseCase:
    return PullCatalogUseCase(
        medusa_service=get_medusa_product_service(),
        allowed_collection_handles=allowed_collection_handles(os.environ),
    )


@lru_cache(maxsize=1)
def get_write_snapshot_use_case() -> WriteSnapshotUseCase:
    return WriteSnapshotUseCase()


@lru_cache(maxsize=1)
def get_meta_catalog_port() -> MetaCatalogPort:
    """Cliente HTTP real contra Meta Graph API `/items_batch`.

    Singleton por proceso (`httpx.AsyncClient` se crea por-llamada dentro
    del cliente — no hay socket leakage; lo que cacheamos es la metadata
    del API version + base URL).
    """
    return MetaCatalogClient()


@lru_cache(maxsize=1)
def get_push_meta_catalog_use_case() -> PushMetaCatalogUseCase:
    return PushMetaCatalogUseCase(meta_port=get_meta_catalog_port())
