"""platform.catalog — port (Protocol) y adapter local (snapshot filesystem).

Cross-agent infrastructure. Consumed by:
  - src/plugins/chats/agent/sales/tools/catalog.py (HU-04) — lectura.
  - src/plugins/catalog/agent/use_cases/write_snapshot.py (HU-03) — escritura.

R-DIP: este paquete NO importa de ningun agente, ni de temporalio, ni de
exoclaw. Solo stdlib y los DTOs internos.
"""
from src.platform.catalog.composition import get_catalog_client
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import (
    CatalogError,
    CatalogUnavailableError,
    ProductNotFoundError,
)
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir
from src.platform.catalog.port import CatalogPort
from src.platform.catalog.variant_attrs import (
    VariantAttrs,
    match_option,
    normalize_label,
    parse_variant_tags,
    split_multi_label,
)

__all__ = [
    "CatalogError",
    "CatalogImageDTO",
    "CatalogManifestDTO",
    "CatalogPort",
    "CatalogPriceDTO",
    "CatalogProductDTO",
    "CatalogUnavailableError",
    "CatalogVariantDTO",
    "LocalSnapshotCatalogClient",
    "ProductNotFoundError",
    "SearchResult",
    "VariantAttrs",
    "get_catalog_client",
    "get_max_age_minutes",
    "get_snapshot_dir",
    "match_option",
    "normalize_label",
    "parse_variant_tags",
    "split_multi_label",
]
