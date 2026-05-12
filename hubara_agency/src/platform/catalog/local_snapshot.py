"""LocalSnapshotCatalogClient — lee del filesystem con cache mtime-aware.

Acepta updates en vivo: al detectar mtime nuevo en `snapshot.json`, recarga.
Sin signals, sin watchdogs, sin restart. La escritura atomica del
`catalog_sync` agent (HU-03, `os.replace(...)`) garantiza que el reader
nunca ve un archivo a medio escribir.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import (
    CatalogUnavailableError,
    ProductNotFoundError,
)

log = logging.getLogger(__name__)

_DEFAULT_MANIFEST = CatalogManifestDTO(
    version="unknown",
    fetched_at="1970-01-01T00:00:00+00:00",
    product_count=0,
)


class LocalSnapshotCatalogClient:
    def __init__(
        self,
        snapshot_dir: Path,
        *,
        max_age_minutes: int = 30,
    ) -> None:
        self._dir = Path(snapshot_dir)
        self._max_age = max_age_minutes
        self._cached_products: list[CatalogProductDTO] | None = None
        self._cached_by_handle: dict[str, CatalogProductDTO] = {}
        self._cached_mtime: float = 0.0
        self._cached_manifest: CatalogManifestDTO = _DEFAULT_MANIFEST

    # ---------- public ----------

    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        """Substring search case-insensitive sobre titulo, handle, tags,
        categorias y description del producto.

        Resuelve los falsos negativos del search literal v1 (ej: cliente
        pregunta 'velas' pero ningun titulo contiene 'velas', solo
        'description' o 'tags'). Empty query (`q=""`) devuelve todo el
        catalogo hasta `limit` — patron deliberado para que el LLM pueda
        responder "que tienen?" con una sola tool call.
        """
        self._ensure_loaded()
        assert self._cached_products is not None  # ensured by _ensure_loaded
        q_lower = q.lower().strip()

        if not q_lower:
            # Empty query → todo el catalogo (sin filtro). Util para
            # "que tienen?" / "muestrame todo".
            matches = list(self._cached_products)
        else:
            matches = [p for p in self._cached_products if _matches(p, q_lower)]

        truncated = len(matches) > limit
        return SearchResult(
            query=q,
            count=len(matches),
            truncated=truncated,
            stale=self._is_stale(),
            manifest=self._cached_manifest,
            results=matches[:limit],
        )

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        # Fast path: by_handle/<h>.json directo (sin parsear snapshot completo).
        per_handle_path = self._dir / "by_handle" / f"{handle}.json"
        if per_handle_path.exists():
            try:
                raw = json.loads(per_handle_path.read_text(encoding="utf-8"))
                return _product_from_raw(raw)
            except json.JSONDecodeError as e:
                raise CatalogUnavailableError(
                    f"by_handle/{handle}.json corrupt: {e}"
                ) from e

        # Slow path: cargar snapshot completo y buscar in-memory.
        self._ensure_loaded()
        if handle in self._cached_by_handle:
            return self._cached_by_handle[handle]
        raise ProductNotFoundError(handle)

    # ---------- internals ----------

    def _ensure_loaded(self) -> None:
        snap_path = self._dir / "snapshot.json"
        if not snap_path.exists():
            raise CatalogUnavailableError(f"snapshot not found at {snap_path}")
        try:
            mtime = snap_path.stat().st_mtime
        except OSError as e:
            raise CatalogUnavailableError(f"snapshot stat failed: {e}") from e

        if self._cached_products is None or mtime > self._cached_mtime:
            try:
                payload = json.loads(snap_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise CatalogUnavailableError(f"snapshot corrupt: {e}") from e
            self._cached_products = [_product_from_raw(r) for r in payload]
            self._cached_by_handle = {
                p.handle: p for p in self._cached_products
            }
            self._cached_mtime = mtime
            self._cached_manifest = self._load_manifest()
            log.info(
                "catalog snapshot reloaded mtime=%.0f products=%d",
                mtime,
                len(self._cached_products),
            )

    def _load_manifest(self) -> CatalogManifestDTO:
        path = self._dir / "manifest.json"
        if not path.exists():
            log.warning(
                "catalog manifest missing at %s — assuming stale", path
            )
            return _DEFAULT_MANIFEST
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning(
                "catalog manifest corrupt: %s — assuming stale", e
            )
            return _DEFAULT_MANIFEST
        return CatalogManifestDTO(
            version=str(payload.get("version", "unknown")),
            fetched_at=str(
                payload.get("fetched_at", _DEFAULT_MANIFEST.fetched_at)
            ),
            product_count=int(payload.get("product_count", 0)),
            source_etag=payload.get("source_etag"),
        )

    def _is_stale(self) -> bool:
        try:
            fetched = datetime.fromisoformat(self._cached_manifest.fetched_at)
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - fetched) > timedelta(minutes=self._max_age)


# ---------- search matcher ----------


def _matches(p: CatalogProductDTO, q_lower: str) -> bool:
    """True si `q_lower` aparece como substring en cualquier campo searchable.

    Campos buscados (todos lowercased):
      - title (ej: "Corona de Redención")
      - handle (ej: "corona")
      - tags (ej: ["Aroma: Lavanda", "Color: Morado"])
      - categories (ej: ["velas", "religiosas"])
      - description (ej: "velitas para quemar en semana santa")
    """
    if q_lower in p.title.lower():
        return True
    if q_lower in p.handle.lower():
        return True
    for t in p.tags:
        if q_lower in t.lower():
            return True
    for c in p.categories:
        if q_lower in c.lower():
            return True
    if p.description and q_lower in p.description.lower():
        return True
    return False


# ---------- raw → DTO ----------


def _product_from_raw(raw: dict[str, Any]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=str(raw["id"]),
        handle=str(raw["handle"]),
        title=str(raw["title"]),
        status=str(raw.get("status", "published")),
        description=raw.get("description"),
        thumbnail=raw.get("thumbnail"),
        variants=[_variant_from_raw(v) for v in raw.get("variants", [])],
        images=[
            CatalogImageDTO(url=i["url"], rank=int(i.get("rank", 0)))
            for i in raw.get("images", [])
        ],
        tags=list(raw.get("tags", [])),
        categories=list(raw.get("categories", [])),
        metadata=(
            {k: str(v) for k, v in (raw.get("metadata") or {}).items()}
            if raw.get("metadata")
            else None
        ),
    )


def _variant_from_raw(raw: dict[str, Any]) -> CatalogVariantDTO:
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
            for p in raw.get("prices", [])
        ],
    )
