"""CatalogKit — el catálogo de productos para plugins (lectura + predicados).

Los plugins que necesitan LEER el catálogo (el port, sus DTOs, sus errores y
los predicados de producto que dependen de datos del catálogo) importan de
acá, no de `src.platform.catalog` (P-28). Es la superficie de drenaje de los
13 imports congelados en `p28_platform_import_allowlist.txt` que apuntan a
`src.platform.catalog*` desde `chats` y `catalog`.

Uso típico (tool de un agente):

    from src.sdk.catalogkit import CatalogPort, ProductNotFoundError

    class MyTool(ToolBase):
        def __init__(self, *, catalog: CatalogPort) -> None: ...

Predicados de producto (2026-09-07, incidente 943e6bff): la política del
color del portavelas SOLO aplica a los productos que traen portavela. La
decisión vive en platform (`catalog/portavelas.py`) porque es metadata de
producto, no lógica del agente; los plugins la consumen por acá.

Mapeos a listas cerradas (motor de decisiones F3): el resolver de categorías
(`resolve_category`) y el de familias de color (`resolve_color_family`,
`family_of_color`) son la regla de hoy, el respaldo, de las capacidades
`categoria` y `familia_de_color` del plugin `chats`.

Fotos del catálogo (2026-09-30): el índice de fotos (`CatalogPhotoIndex`,
`get_catalog_photo_index`) guarda el vector de cada foto para encontrar los
productos más parecidos a la foto de un cliente (visión de ventas).

Regla 1 del SDK: `from x import y as y` — sin el `as y`, ruff --fix poda el
re-export.
"""
from __future__ import annotations

from src.platform.catalog.categories import (
    CatalogCategoryDTO as CatalogCategoryDTO,
    CategoryResolution as CategoryResolution,
    resolve_category as resolve_category,
)
from src.platform.catalog.color_families import (
    ColorFamilies as ColorFamilies,
    family_of_color as family_of_color,
    resolve_color_family as resolve_color_family,
)
from src.platform.catalog.composition import (
    get_catalog_client as get_catalog_client,
    get_catalog_photo_index as get_catalog_photo_index,
    get_photo_color_store as get_photo_color_store,
)
from src.platform.catalog.dtos import (
    CatalogImageDTO as CatalogImageDTO,
    CatalogPriceDTO as CatalogPriceDTO,
    CatalogProductDTO as CatalogProductDTO,
    CatalogVariantDTO as CatalogVariantDTO,
    SearchResult as SearchResult,
)
from src.platform.catalog.errors import (
    CatalogError as CatalogError,
    CatalogUnavailableError as CatalogUnavailableError,
    ProductNotFoundError as ProductNotFoundError,
)
from src.platform.catalog.photo_index import (
    CatalogPhoto as CatalogPhoto,
    CatalogPhotoIndex as CatalogPhotoIndex,
    PhotoCandidate as PhotoCandidate,
    catalog_photos as catalog_photos,
    fetch_catalog_photo as fetch_catalog_photo,
)
from src.platform.catalog.photo_colors import (
    PhotoColorRecord as PhotoColorRecord,
    VaultPhotoColorStore as VaultPhotoColorStore,
    color_of_photo as color_of_photo,
)
from src.platform.catalog.port import CatalogPort as CatalogPort
from src.platform.catalog.portavelas import (
    PORTAVELAS_METADATA_KEY as PORTAVELAS_METADATA_KEY,
    order_includes_portavelas as order_includes_portavelas,
    product_includes_portavelas as product_includes_portavelas,
)
from src.platform.catalog.variant_attrs import (
    normalize_label as normalize_label,
)
