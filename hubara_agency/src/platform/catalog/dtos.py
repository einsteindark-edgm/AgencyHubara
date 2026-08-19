"""DTOs JSON-safe del catalogo. R-JSON-ready desde dia 1.

Decimal → str para evitar la trampa de JSON serialization. Las activities
de HU-03 retornan estos DTOs cruzando workflow boundary sin violar R-JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita el ciclo dtos ↔ categories en runtime
    from src.platform.catalog.categories import CategoryResolution


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
    # slug → nombre real de la categoría en Medusa ("velas-religiosas" →
    # "Velas Religiosas"). None = snapshot viejo (pre filtro por categoría);
    # el resolver cae al deslugify del slug.
    category_labels: dict[str, str] | None = None
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
    # Presente solo cuando el caller pidió filtrar por categoría. Dice QUÉ
    # categoría se resolvió (o por qué no) para que el agente no invente.
    category: "CategoryResolution | None" = None


def product_dto_from_raw(raw: dict) -> CatalogProductDTO:
    """Reconstruye un `CatalogProductDTO` desde su dict serializado (asdict).

    ÚNICO parser raw→DTO del catálogo — lo comparten el read-path del
    snapshot (`local_snapshot`) y el push a Meta (`push_meta_catalog`).
    Lección del primer sync post-#178 (2026-07-16): había DOS parsers
    duplicados y el del push no mapeaba `options` → el mapper veía
    `options=None` y publicaba un item por producto en vez de per-variante.
    Backward-compat: keys ausentes (snapshots viejos) → defaults/None.
    """
    return CatalogProductDTO(
        id=str(raw["id"]),
        handle=str(raw["handle"]),
        title=str(raw["title"]),
        status=str(raw.get("status", "published")),
        description=raw.get("description"),
        thumbnail=raw.get("thumbnail"),
        variants=[variant_dto_from_raw(v) for v in raw.get("variants") or []],
        images=[
            CatalogImageDTO(url=i["url"], rank=int(i.get("rank", 0)))
            for i in raw.get("images") or []
        ],
        tags=list(raw.get("tags") or []),
        categories=list(raw.get("categories") or []),
        category_labels=(
            {str(k): str(v) for k, v in raw["category_labels"].items()}
            if raw.get("category_labels")
            else None
        ),
        metadata=(
            {k: str(v) for k, v in (raw.get("metadata") or {}).items()}
            if raw.get("metadata")
            else None
        ),
        options=(
            {
                str(k): [str(v) for v in vals]
                for k, vals in raw["options"].items()
            }
            if raw.get("options")
            else None
        ),
    )


def variant_dto_from_raw(raw: dict) -> CatalogVariantDTO:
    return CatalogVariantDTO(
        id=str(raw["id"]),
        title=str(raw["title"]),
        sku=raw.get("sku"),
        prices=[
            CatalogPriceDTO(
                amount=str(p["amount"]),
                currency_code=str(p["currency_code"]),
                min_quantity=p.get("min_quantity"),
                max_quantity=p.get("max_quantity"),
            )
            for p in raw.get("prices") or []
        ],
        options=(
            {str(k): str(v) for k, v in raw["options"].items()}
            if raw.get("options")
            else None
        ),
    )
