"""Tools de catálogo del connector (lectura, canal 1: ``CatalogPort`` del SDK).

Devuelven dicts JSON cerrados: MBA solo puede citar handles, precios, aromas,
colores y diseños que vengan de acá (regla 1 del skill ``uso-de-herramientas``).
Los envelopes siguen la forma probada del agente Sales de Hubara (COP primero
en multi-moneda, portada excluida de los diseños, listas cerradas parseadas de
los tags) para que el comportamiento observable sea el mismo con MBA al frente.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from loguru import logger

from src.sdk.connectorkit import (
    CatalogUnavailableError,
    ProductNotFoundError,
    deslugify,
    parse_variant_colors,
    parse_variant_tags,
)
from src.sdk.mediakit import derive_image_label, fold_for_match

MAX_LIMIT = 30
DEFAULT_LIMIT = 10
#: alfabeto de un handle de Medusa. Un handle fuera de esto no existe y NO se
#: le pasa al port (el snapshot lo resuelve a un archivo: sin esto,
#: `../../x` sería un oráculo de existencia de archivos desde un endpoint público).
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")

_UNAVAILABLE = {
    "error": "catalog_unavailable",
    "message": (
        "El catálogo no está disponible en este momento. Reintenta una vez; si vuelve a fallar, "
        "pasa el caso a un colega con escalate_to_human (reason_category=CATALOG_GAP)."
    ),
}


def _unavailable(reason: str) -> dict[str, Any]:
    # `reason` es un código cerrado: el texto de la excepción (paths, hosts)
    # va al log, nunca al agente (que podría repetírselo al cliente).
    return {**_UNAVAILABLE, "detail": reason}


async def search_products(
    catalog: Any | None,
    *,
    q: str = "",
    category: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if catalog is None:
        return _unavailable("catalog_not_configured")
    limit = max(1, min(MAX_LIMIT, int(limit)))
    try:
        extra = {"category": category} if category is not None else {}
        result = await catalog.search(q=q, limit=limit, **extra)
    except CatalogUnavailableError as exc:
        logger.error("[mba] search_products catalog_unavailable: {}", exc)
        return _unavailable("catalog_unavailable")
    envelope: dict[str, Any] = {
        "query": result.query,
        "count": result.count,
        "truncated": result.truncated,
        "stale": result.stale,
        "manifest": asdict(result.manifest),
        "results": [_product_summary(p) for p in result.results],
    }
    if result.category is not None:
        envelope["category"] = await _category_block(catalog, result.category)
    return envelope


async def _category_block(catalog: Any, resolution: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "query": resolution.query,
        "matched": resolution.matched.label if resolution.matched else None,
        "confidence": resolution.confidence,
    }
    if resolution.confidence == "no_categories":
        block["message"] = (
            "Este catálogo no tiene categorías cargadas; el término se buscó como texto."
        )
        return block
    if resolution.matched is None:
        block["candidates"] = [c.label for c in resolution.candidates]
        try:
            block["available"] = [c.label for c in await catalog.list_categories()]
        except CatalogUnavailableError:
            block["available"] = []
        block["message"] = (
            "No se reconoció esa categoría. Ofrece al cliente SOLO las de candidates (si hay) o las de "
            "available; nunca digas que no se maneja algo sin mirar esa lista."
        )
    return block


async def list_categories(catalog: Any | None) -> dict[str, Any]:
    if catalog is None:
        return _unavailable("catalog_not_configured")
    try:
        categories = await catalog.list_categories()
    except CatalogUnavailableError as exc:
        logger.error("[mba] list_categories catalog_unavailable: {}", exc)
        return _unavailable("catalog_unavailable")
    return {
        "count": len(categories),
        "categories": [
            {"name": c.label, "product_count": c.product_count} for c in categories
        ],
    }


async def get_product_by_handle(catalog: Any | None, *, handle: str) -> dict[str, Any]:
    if catalog is None:
        return _unavailable("catalog_not_configured")
    not_found = {
        "found": False,
        "message": "Ese handle no existe en el catálogo. Usa search_products para descubrir productos.",
    }
    if not _HANDLE_RE.match(handle):
        return not_found
    try:
        product = await catalog.get_by_handle(handle)
    except ProductNotFoundError:
        return not_found
    except CatalogUnavailableError as exc:
        logger.error("[mba] get_product_by_handle catalog_unavailable: {}", exc)
        return _unavailable("catalog_unavailable")
    return {"found": True, "product": _product_full(product)}


# ---------- envelopes ----------


def _product_summary(p: Any) -> dict[str, Any]:
    price, currency = _first_price(p)
    attrs = parse_variant_tags(p.tags)
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "price": price,
        "currency": currency,
        "in_stock": True,
        "thumbnail_url": p.thumbnail,
        "tags": p.tags,
        "categories": _category_labels(p),
        "aromas": attrs.aromas,
        "colors": attrs.colors,
        "designs": _designs_for(p),
        "variants": (
            [{"id": v.id, "title": v.title} for v in p.variants]
            if p.options and len(p.variants) > 1
            else []
        ),
    }


def _product_full(p: Any) -> dict[str, Any]:
    attrs = parse_variant_tags(p.tags)
    envelope: dict[str, Any] = {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "description": p.description,
        "thumbnail": p.thumbnail,
        "aromas": attrs.aromas,
        "colors": attrs.colors,
        "designs": _designs_for(p),
        "options": p.options,
        "variants": [
            {
                "id": v.id,
                "title": v.title,
                "sku": v.sku,
                "price": _variant_price(v)[0],
                "currency": _variant_price(v)[1],
                "options": v.options,
            }
            for v in p.variants
        ],
        "images": [
            {"url": i.url, "rank": i.rank, "label": derive_image_label(i.url)}
            for i in p.images
        ],
        "tags": p.tags,
        "categories": _category_labels(p),
    }
    variant_colors = parse_variant_colors(p.metadata)
    if variant_colors:
        envelope["variant_colors"] = variant_colors
    return envelope


def _category_labels(p: Any) -> list[str]:
    labels = p.category_labels or {}
    return [labels.get(slug) or deslugify(slug) for slug in p.categories]


def _designs_for(p: Any) -> list[str]:
    designs: list[str] = []
    for img in p.images:
        label = derive_image_label(img.url)
        if label and label not in designs:
            designs.append(label)
    if p.options and len(p.variants) > 1:
        allowed = {
            fold_for_match(value) for values in p.options.values() for value in values
        }
        filtered = [d for d in designs if fold_for_match(d) in allowed]
        if filtered:
            return filtered
    return designs


def _cop_first(prices: list[Any]) -> tuple[str | None, str | None]:
    for price in prices:
        if price.currency_code.lower() == "cop":
            return price.amount, price.currency_code
    if prices:
        return prices[0].amount, prices[0].currency_code
    return None, None


def _variant_price(v: Any) -> tuple[str | None, str | None]:
    return _cop_first(v.prices)


def _first_price(p: Any) -> tuple[str | None, str | None]:
    if not p.variants:
        return None, None
    return _cop_first(p.variants[0].prices)
