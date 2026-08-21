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

from src.platform.catalog.categories import (
    CatalogCategoryDTO,
    CategoryResolution,
    collect_categories,
    resolve_category,
    text_matches_loosely,
)
from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogProductDTO,
    SearchResult,
    product_dto_from_raw,
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

    async def list_categories(self) -> list[CatalogCategoryDTO]:
        """Closed-list de categorías del snapshot (con conteo por categoría)."""
        self._ensure_loaded()
        assert self._cached_products is not None  # ensured by _ensure_loaded
        return collect_categories(self._cached_products)

    async def search(
        self, q: str, *, limit: int = 10, category: str | None = None
    ) -> SearchResult:
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

        universe = self._cached_products
        resolution: CategoryResolution | None = None
        if category is not None:
            known = collect_categories(self._cached_products)
            if not known:
                # El catálogo no tiene categorías cargadas en Medusa. Dejar al
                # cliente sin respuesta sería peor que buscar su término como
                # texto — degradación explícita, no silenciosa.
                resolution = CategoryResolution(
                    query=category, confidence="no_categories"
                )
                term = q.strip() or category.strip()
                universe = [
                    p for p in universe if text_matches_loosely(term, _haystack(p))
                ]
                q_lower = ""
            else:
                # Filtro de PERTENENCIA (product.categories), no de texto: el
                # substring search también pegaba contra description y devolvía
                # productos de otra categoría.
                resolution = resolve_category(category, known)
                if resolution.matched is None:
                    universe = []
                else:
                    slug = resolution.matched.slug
                    universe = [p for p in universe if slug in p.categories]

        if not q_lower:
            # Empty query → todo el universo (sin filtro de texto). Util para
            # "que tienen?" / "muestrame todo".
            matches = list(universe)
        else:
            matches = [p for p in universe if _matches(p, q_lower)]

        truncated = len(matches) > limit
        return SearchResult(
            query=q,
            count=len(matches),
            truncated=truncated,
            stale=self._is_stale(),
            manifest=self._cached_manifest,
            results=matches[:limit],
            category=resolution,
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


def _haystack(p: CatalogProductDTO) -> str:
    """Todo el texto searchable del producto, en un solo string."""
    return " ".join(
        [p.title, p.handle, *p.tags, *p.categories, p.description or ""]
    )


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


# Parser raw→DTO canónico compartido con el push (lección del primer sync
# post-#178: dos copias divergieron y el push perdió `options`).
_product_from_raw = product_dto_from_raw
