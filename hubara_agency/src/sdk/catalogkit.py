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

Regla 1 del SDK: `from x import y as y` — sin el `as y`, ruff --fix poda el
re-export.
"""
from __future__ import annotations

from src.platform.catalog.composition import (
    get_catalog_client as get_catalog_client,
)
from src.platform.catalog.dtos import (
    CatalogProductDTO as CatalogProductDTO,
    CatalogVariantDTO as CatalogVariantDTO,
    SearchResult as SearchResult,
)
from src.platform.catalog.errors import (
    CatalogError as CatalogError,
    CatalogUnavailableError as CatalogUnavailableError,
    ProductNotFoundError as ProductNotFoundError,
)
from src.platform.catalog.port import CatalogPort as CatalogPort
from src.platform.catalog.portavelas import (
    PORTAVELAS_METADATA_KEY as PORTAVELAS_METADATA_KEY,
    order_includes_portavelas as order_includes_portavelas,
    product_includes_portavelas as product_includes_portavelas,
)
