"""DTOs JSON-safe del catalogo. R-JSON-ready desde dia 1.

Decimal → str para evitar la trampa de JSON serialization. Las activities
de HU-03 retornan estos DTOs cruzando workflow boundary sin violar R-JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogPriceDTO:
    amount: str  # Decimal serialized (R-JSON-safe)
    currency_code: str
    min_quantity: int | None = None
    max_quantity: int | None = None


@dataclass(frozen=True)
class CatalogVariantDTO:
    id: str
    title: str
    sku: str | None = None
    prices: list[CatalogPriceDTO] = field(default_factory=list)
    # Option values de la variante ({"Signo": "Leo"}). None = producto
    # legacy sin options reales (variante única "Unico" + tags).
    options: dict[str, str] | None = None


@dataclass(frozen=True)
class CatalogImageDTO:
    url: str
    rank: int = 0


@dataclass(frozen=True)
class CatalogProductDTO:
    id: str
    handle: str
    title: str
    status: str
    description: str | None = None
    thumbnail: str | None = None
    variants: list[CatalogVariantDTO] = field(default_factory=list)
    images: list[CatalogImageDTO] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    metadata: dict[str, str] | None = None
    # Ejes de selección reales del producto ({"Signo": ["Aries", ...]}).
    # None = producto sin options en Medusa (snapshots viejos también).
    options: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class CatalogManifestDTO:
    version: str
    fetched_at: str  # ISO 8601 UTC
    product_count: int
    source_etag: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Closed-list grounding envelope retornado por CatalogPort.search()."""
    query: str
    count: int
    truncated: bool
    stale: bool
    manifest: CatalogManifestDTO
    results: list[CatalogProductDTO] = field(default_factory=list)
