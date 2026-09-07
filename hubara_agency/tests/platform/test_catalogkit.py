"""Regla de oro del SDK: `src.sdk.catalogkit` re-exporta el port del catálogo,
sus DTOs/errores y los predicados de producto — mismo patrón que mediakit.

Consumidor: `RegisterOrderTool` (plugin `chats`) decide contra el catálogo si
el pedido trae portavela (incidente 943e6bff) sin importar `src.platform`
directo (P-28).

El check es por IDENTIDAD (`is`): la fachada re-exporta EL MISMO objeto que
platform, no una re-implementación.
"""
from __future__ import annotations


def test_catalogkit_reexports_port_dtos_and_errors():
    import src.platform.catalog.composition as composition
    import src.platform.catalog.dtos as dtos
    import src.platform.catalog.errors as errors
    import src.platform.catalog.port as port
    import src.sdk.catalogkit as kit

    assert kit.CatalogPort is port.CatalogPort
    assert kit.CatalogProductDTO is dtos.CatalogProductDTO
    assert kit.CatalogVariantDTO is dtos.CatalogVariantDTO
    assert kit.SearchResult is dtos.SearchResult
    assert kit.CatalogError is errors.CatalogError
    assert kit.CatalogUnavailableError is errors.CatalogUnavailableError
    assert kit.ProductNotFoundError is errors.ProductNotFoundError
    assert kit.get_catalog_client is composition.get_catalog_client


def test_catalogkit_reexports_portavelas_predicates():
    """La decisión "¿este producto trae portavela?" vive en platform (es
    metadata de producto); los plugins la consumen vía este kit."""
    import src.platform.catalog.portavelas as impl
    import src.sdk.catalogkit as kit

    assert kit.product_includes_portavelas is impl.product_includes_portavelas
    assert kit.order_includes_portavelas is impl.order_includes_portavelas
    assert kit.PORTAVELAS_METADATA_KEY is impl.PORTAVELAS_METADATA_KEY
