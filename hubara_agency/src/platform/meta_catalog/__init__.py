"""Platform — sincronización de catálogo a Meta Commerce Catalog.

Cobertura:

* `dtos.MetaCatalogItem` — el shape que Meta espera por producto
* `mapper.map_product_to_meta` — `CatalogProductDTO` → `MetaCatalogItem`
* `client.MetaCatalogClient` — HTTP client a Graph API /items_batch
* `port.MetaCatalogPort` — interfaz
* `composition.get_meta_catalog_port` — factory

El pipeline (Parte B):

  Medusa → PullCatalogUseCase → CatalogProductDTO[]
                                       ├→ WriteSnapshotUseCase (existing)
                                       └→ PushMetaCatalogUseCase ★ NEW
                                              └→ MetaCatalogPort.upsert_batch()

Multi-tenant: cada tenant tiene un `meta_catalog_id` + `meta_system_user_token`
en su config (agents_admin plugin). La activity push_to_meta_catalog recibe
estos identifiers y los pasa al port.

Rate limits Meta: ~200 calls/h en niveles bajos. Backoff exponencial en
429/4/17. Activity heartbeat cada 10s durante el batch.
"""
from src.platform.meta_catalog.dtos import (
    MetaCatalogItem,
    MetaBatchRequest,
    MetaBatchResult,
    MetaBatchItemResult,
)
from src.platform.meta_catalog.port import MetaCatalogPort

__all__ = [
    "MetaCatalogItem",
    "MetaBatchRequest",
    "MetaBatchResult",
    "MetaBatchItemResult",
    "MetaCatalogPort",
]
