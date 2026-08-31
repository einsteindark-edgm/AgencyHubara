"""Tools del agente Sales para consultar el catalogo de productos.

Closed-list grounding: las tools devuelven envelopes JSON cerrados.
El system prompt (`workspace/TOOLS.md`) instruye al LLM a solo citar
productos cuyo `handle` aparezca en el ultimo `tool_result`.

ADR-001 alignment: estas tools son inertes respecto a Temporal — solo
leen del CatalogPort. NO escriben metadata, NO emiten decisions.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.catalog import (
    CatalogPort,
    CatalogProductDTO,
    CatalogUnavailableError,
    CatalogVariantDTO,
    ProductNotFoundError,
    SearchResult,
    deslugify,
    parse_variant_colors,
    parse_variant_tags,
)
from src.sdk.mediakit import derive_image_label, fold_for_match


class SearchProductsTool(ToolBase):
    """Busca productos del catalogo por substring en title/handle."""

    name = "search_products"
    description = (
        "Busca productos del catálogo de Hubara. El search es case-insensitive "
        "y matchea en title, handle, tags, categorías y description del "
        "producto. Pasa `q=\"\"` (string vacío) para LISTAR TODO el catálogo "
        "(útil cuando el cliente pregunta '¿qué tienen?'). Pasa `q=\"<tema>\"` "
        "para filtrar (ej: 'lavanda', 'religiosa', 'cera de palma'). Retorna "
        "hasta `limit` productos con precio, handle, imagen y tags."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": (
                    "Texto de búsqueda. Substring case-insensitive contra "
                    "title, handle, tags, categorías y description. "
                    "Pasa string vacío (\"\") para LISTAR TODO el catálogo."
                ),
                "maxLength": 100,
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Máximo de productos a retornar (default 10, máximo 30 "
                    "para listar todo el catálogo)."
                ),
                "minimum": 1,
                "maximum": 30,
                "default": 10,
            },
            "category": {
                "type": "string",
                "description": (
                    "Categoría pedida por el cliente, TAL CUAL la escribió "
                    "(ej: 'religiosas', 'velas religosas', 'difusor'). El "
                    "sistema la resuelve contra las categorías reales "
                    "tolerando typos, plurales y nombres parciales, y filtra "
                    "SOLO los productos que pertenecen a ella. Úsalo SIEMPRE "
                    "que el cliente pida 'las X' o 'productos de X' en vez de "
                    "meter la categoría en `q`."
                ),
                "maxLength": 100,
            },
        },
        "required": ["q"],
    }

    def __init__(
        self, workspace: str | Path, catalog: CatalogPort
    ) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self, ctx: ToolContext, q: str, limit: int = 10,
        category: str | None = None,
    ) -> str:
        logger.info(
            "🔍 [TOOL search_products] session={} q={!r} limit={} category={!r}",
            ctx.session_key, q, limit, category,
        )
        try:
            # `category` solo viaja cuando el cliente pidió una: mantiene
            # compatible cualquier CatalogPort que no implemente el filtro.
            extra = {"category": category} if category is not None else {}
            result: SearchResult = await self._catalog.search(
                q=q, limit=limit, **extra
            )
        except CatalogUnavailableError as e:
            logger.error(
                "🔍 [TOOL search_products] catalog_unavailable: {}", e
            )
            return json.dumps(
                {
                    "error": "catalog_unavailable",
                    "message": (
                        "El catálogo no está disponible en este momento. "
                        "Pídele al cliente unos minutos y reintenta."
                    ),
                    "detail": str(e),
                },
                ensure_ascii=False,
            )

        logger.info(
            "🔍 [TOOL search_products] → count={} truncated={} stale={} "
            "category={} handles={}",
            result.count, result.truncated, result.stale,
            (result.category.matched.slug
             if result.category and result.category.matched else None),
            [p.handle for p in result.results],
        )
        envelope: dict[str, Any] = {
            "query": result.query,
            "count": result.count,
            "truncated": result.truncated,
            "stale": result.stale,
            "manifest": asdict(result.manifest),
            "results": [_product_summary(p) for p in result.results],
        }
        if result.category is not None:
            envelope["category"] = await self._category_block(result)
        return json.dumps(envelope, ensure_ascii=False)

    async def _category_block(self, result: SearchResult) -> dict[str, Any]:
        """Qué categoría se resolvió — y si no, cuáles existen.

        Sin `available` el LLM negaba categorías que SÍ existen cuando el
        cliente las escribía distinto (misma forma que el caso "leo").
        """
        resolution = result.category
        assert resolution is not None
        block: dict[str, Any] = {
            "query": resolution.query,
            "matched": (
                resolution.matched.label if resolution.matched else None
            ),
            "confidence": resolution.confidence,
        }
        if resolution.confidence == "no_categories":
            # El catálogo no tiene categorías cargadas: ya se buscó el término
            # como texto. Ofrecer una lista vacía sería peor que no ofrecer.
            block["message"] = (
                "Este catálogo no tiene categorías cargadas — busqué ese "
                "término como texto. Muestra estos resultados o refina con `q`."
            )
            return block
        if resolution.matched is None:
            block["candidates"] = [c.label for c in resolution.candidates]
            block["available"] = [
                c.label for c in await self._catalog.list_categories()
            ]
            block["message"] = (
                "No reconocí esa categoría. Ofrécele al cliente SOLO las de "
                "`candidates` (si hay) o las de `available` — nunca digas que "
                "no manejamos algo sin mirar esa lista."
            )
        return block


class ListCategoriesTool(ToolBase):
    """Closed-list de categorías reales del catálogo."""

    name = "list_categories"
    description = (
        "Devuelve las categorías REALES del catálogo con cuántos productos "
        "tiene cada una. Úsala cuando el cliente pregunta '¿qué categorías "
        "tienen?' o cuando `search_products` no reconoció la categoría que "
        "pidió. Es una lista CERRADA: cualquier categoría fuera de ella no "
        "existe, y ninguna de ella puede negarse."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(self, ctx: ToolContext) -> str:
        logger.info("🗂️ [TOOL list_categories] session={}", ctx.session_key)
        try:
            categories = await self._catalog.list_categories()
        except CatalogUnavailableError as e:
            logger.error("🗂️ [TOOL list_categories] catalog_unavailable: {}", e)
            return json.dumps(
                {
                    "error": "catalog_unavailable",
                    "message": (
                        "El catálogo no está disponible en este momento. "
                        "Pídele al cliente unos minutos y reintenta."
                    ),
                    "detail": str(e),
                },
                ensure_ascii=False,
            )
        logger.info(
            "🗂️ [TOOL list_categories] → {}",
            [c.slug for c in categories],
        )
        return json.dumps(
            {
                "count": len(categories),
                "categories": [
                    {"name": c.label, "product_count": c.product_count}
                    for c in categories
                ],
            },
            ensure_ascii=False,
        )


class GetProductByHandleTool(ToolBase):
    """Devuelve el detalle exacto de un producto por su handle."""

    name = "get_product_by_handle"
    description = (
        "Devuelve el detalle completo de UN producto cuyo handle ya conoces "
        "(visto en search_products). Úsalo para confirmar precio, "
        "descripción y variantes ANTES de cerrar venta. NO inventes handles — "
        "si el cliente menciona un producto, primero busca con search_products."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": (
                    "Handle (slug) exacto del producto. Solo handles vistos "
                    "en search_products."
                ),
                "minLength": 1,
                "maxLength": 200,
            },
        },
        "required": ["handle"],
    }

    def __init__(
        self, workspace: str | Path, catalog: CatalogPort
    ) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self, ctx: ToolContext, handle: str
    ) -> str:
        logger.info(
            "📦 [TOOL get_product_by_handle] session={} handle={!r}",
            ctx.session_key, handle,
        )
        try:
            product = await self._catalog.get_by_handle(handle)
        except ProductNotFoundError:
            logger.warning(
                "📦 [TOOL get_product_by_handle] NOT FOUND handle={!r} "
                "— LLM debe usar search_products primero",
                handle,
            )
            return json.dumps(
                {
                    "found": False,
                    "message": (
                        f"El handle '{handle}' no existe en el catálogo. "
                        "Usa search_products para descubrir productos "
                        "disponibles."
                    ),
                },
                ensure_ascii=False,
            )
        except CatalogUnavailableError as e:
            logger.error(
                "📦 [TOOL get_product_by_handle] catalog_unavailable: {}", e
            )
            return json.dumps(
                {
                    "error": "catalog_unavailable",
                    "message": (
                        "El catálogo no está disponible en este momento. "
                        "Pídele al cliente unos minutos y reintenta."
                    ),
                    "detail": str(e),
                },
                ensure_ascii=False,
            )

        logger.info(
            "📦 [TOOL get_product_by_handle] → FOUND handle={!r} title={!r}",
            product.handle, product.title,
        )
        return json.dumps(
            {
                "found": True,
                "product": _product_full(product),
            },
            ensure_ascii=False,
        )


# ---------- envelope helpers ----------


def _product_summary(p: CatalogProductDTO) -> dict[str, Any]:
    """Version liviana para search_products (sin description larga)."""
    price, currency = _first_price(p)
    attrs = parse_variant_tags(p.tags)
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "price": price,
        "currency": currency,
        "in_stock": True,  # v1: asumimos True. Stock real-time es follow-up.
        "thumbnail_url": p.thumbnail,
        "tags": p.tags,
        # Nombres reales de las categorías del producto (no los slugs): el
        # LLM los cita al cliente y los usa para "muéstrame más de estas".
        "categories": _category_labels(p),
        # Listas CERRADAS ya parseadas de los tags (caso ep_010: el LLM recitaba
        # "14 aromas y 10 colores" parseando mal los prefijos). Estos son LOS
        # aromas/colores que existen — cualquier otro es invento.
        "aromas": attrs.aromas,
        "colors": attrs.colors,
        # Diseños nombrados en los filenames de las fotos (caso wa_573125671604:
        # el LLM negó tener "leo" cuando la foto leo-*.webp existía). Lista
        # cerrada: cualquier diseño fuera de esta lista es invento.
        "designs": _designs_for(p),
        # Ids de variante SOLO para productos con options: el carrito inbound
        # de WhatsApp trae `variant_...` como retailer_id (Meta per-variante,
        # PR #178/#179) y el LLM lo resuelve contra esta lista. Legacy → [].
        "variants": (
            [{"id": v.id, "title": v.title} for v in p.variants]
            if p.options and len(p.variants) > 1
            else []
        ),
    }


def _product_full(p: CatalogProductDTO) -> dict[str, Any]:
    """Version completa para get_product_by_handle."""
    attrs = parse_variant_tags(p.tags)
    # Mapeo signo→color declarado por el operador (metadata "colores"). Cada
    # variante viene en UN color fijo: si el cliente pide un color que no es
    # el del signo elegido, el mapeo permite ofrecer el mismo color en otro
    # signo (aclarando que el signo es distinto) en vez de negar o inventar.
    variant_colors = parse_variant_colors(p.metadata)
    envelope = {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "description": p.description,
        "thumbnail": p.thumbnail,
        "aromas": attrs.aromas,
        "colors": attrs.colors,
        "designs": _designs_for(p),
        # Ejes de selección reales (Medusa options). Producto legacy → None.
        # Cuando existe, ESTA es la closed-list de variantes elegibles.
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
    if variant_colors:
        envelope["variant_colors"] = variant_colors
    return envelope


def _category_labels(p: CatalogProductDTO) -> list[str]:
    """slug → nombre real; snapshots viejos (sin labels) → deslugify."""
    labels = p.category_labels or {}
    return [labels.get(slug) or deslugify(slug) for slug in p.categories]


def _designs_for(p: CatalogProductDTO) -> list[str]:
    """Diseños únicos derivados de los filenames de las fotos, en orden de
    rank. Un producto con fotos genéricas (img1.webp) devuelve lista vacía.

    Producto con options reales (Duo Zodiacal v2): solo cuentan los labels
    que son option values — la portada (`00-portada-*.webp`) y cualquier
    otra foto decorativa NO son diseños elegibles.
    """
    designs: list[str] = []
    for img in p.images:
        label = derive_image_label(img.url)
        if label and label not in designs:
            designs.append(label)
    if p.options and len(p.variants) > 1:
        # Comparación accent-insensitive (premortem §4.7): option "Géminis"
        # vs label de filename "Geminis" deben matchear.
        allowed = {
            fold_for_match(value)
            for values in p.options.values()
            for value in values
        }
        filtered = [d for d in designs if fold_for_match(d) in allowed]
        if filtered:
            return filtered
    return designs


def _variant_price(v: CatalogVariantDTO) -> tuple[str | None, str | None]:
    """Precio de UNA variante, COP primero en multi-currency (mismo criterio
    que `_first_price`; run 33a8dd9f mostró `currency: usd` en el detalle)."""
    for price in v.prices:
        if price.currency_code.lower() == "cop":
            return (price.amount, price.currency_code)
    if v.prices:
        return (v.prices[0].amount, v.prices[0].currency_code)
    return (None, None)


def _first_price(p: CatalogProductDTO) -> tuple[str | None, str | None]:
    if not p.variants:
        return (None, None)
    v = p.variants[0]
    if not v.prices:
        return (None, None)
    # La tienda vende en COP — si el producto también tiene precio en otra
    # moneda (Medusa multi-currency), COP gana (caso wa_573125671604: la
    # caption salió "$35.000 usd" por agarrar prices[0] ciego).
    for price in v.prices:
        if price.currency_code.lower() == "cop":
            return (price.amount, price.currency_code)
    return (v.prices[0].amount, v.prices[0].currency_code)
