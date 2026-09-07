"""¿Este producto trae portavela? — decisión determinista contra el catálogo.

Incidente 943e6bff (2026-09-07): el cliente cerró un pedido sin portavelas y
recibió "Al finalizar el pago del pedido se escogen los colores del
portavelas, según disponibilidad". La política del color del portavelas
(según disponibilidad, se define al finalizar el pago) SOLO aplica a los
productos que lo incluyen — hoy, en el catálogo vivo, únicamente el Dúo
Zodiacal ("un plato portavela de concreto pulido" en su description).

Fuente de verdad: el CATÁLOGO, nunca el LLM.
  1. `metadata.portavelas` explícito gana ("true"/"false" y sinónimos) —
     el operador puede prender/apagar la política por producto en Medusa.
  2. Sin flag: la mención "portavela" en título o description decide.

Puro, stdlib-only (R-DIP: sin I/O, sin agentes).
"""
from __future__ import annotations

from collections.abc import Iterable

from src.platform.catalog.dtos import CatalogProductDTO

PORTAVELAS_METADATA_KEY = "portavelas"

_TRUTHY = frozenset({"true", "1", "yes", "si", "sí", "on"})
_FALSY = frozenset({"false", "0", "no", "off"})
_MENTION = "portavela"  # cubre "portavela" y "portavelas"


def product_includes_portavelas(product: CatalogProductDTO) -> bool:
    """True si el producto trae portavela según el catálogo."""
    flag = (product.metadata or {}).get(PORTAVELAS_METADATA_KEY)
    if flag is not None:
        normalized = str(flag).strip().casefold()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
    text = f"{product.title or ''} {product.description or ''}".casefold()
    return _MENTION in text


def order_includes_portavelas(products: Iterable[CatalogProductDTO]) -> bool:
    """True si ALGÚN producto del pedido trae portavela."""
    return any(product_includes_portavelas(p) for p in products)
