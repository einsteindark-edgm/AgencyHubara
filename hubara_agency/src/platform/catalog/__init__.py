"""platform.catalog — port (Protocol) y adapter local (snapshot filesystem).

Cross-agent infrastructure. Consumed by:
  - src/plugins/chats/agent/sales/tools/catalog.py (HU-04) — lectura.
  - src/plugins/catalog/agent/use_cases/write_snapshot.py (HU-03) — escritura.

R-DIP: este paquete NO importa de ningun agente, ni de temporalio, ni de
exoclaw. Solo stdlib y los DTOs internos.
"""
from src.platform.catalog.categories import (
    CatalogCategoryDTO,
    CategoryResolution,
    collect_categories,
    deslugify,
    resolve_category,
)
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
from src.platform.catalog.variant_colors import (
    COLORS_METADATA_KEY,
    colors_for_value,
    matching_color_alias,
    parse_variant_colors,
    primary_colors,
    values_for_color,
)

__all__ = [
    "COLORS_METADATA_KEY",
    "CatalogCategoryDTO",
    "CatalogError",
    "CatalogImageDTO",
    "CatalogManifestDTO",
    "CatalogPort",
    "CatalogPriceDTO",
    "CatalogProductDTO",
    "CatalogUnavailableError",
    "CatalogVariantDTO",
    "CategoryResolution",
    "LocalSnapshotCatalogClient",
    "ProductNotFoundError",
    "SearchResult",
    "VariantAttrs",
    "collect_categories",
    "colors_for_value",
    "deslugify",
    "get_catalog_client",
    "get_max_age_minutes",
    "get_snapshot_dir",
    "match_option",
    "matching_color_alias",
    "normalize_label",
    "parse_variant_colors",
    "parse_variant_tags",
    "primary_colors",
    "resolve_category",
    "split_multi_label",
    "values_for_color",
]
