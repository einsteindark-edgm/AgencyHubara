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
    ProductNotFoundError,
    SearchResult,
)


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
        },
        "required": ["q"],
    }

    def __init__(
        self, workspace: str | Path, catalog: CatalogPort
    ) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self, ctx: ToolContext, q: str, limit: int = 10
    ) -> str:
        logger.info(
            "🔍 [TOOL search_products] session={} q={!r} limit={}",
            ctx.session_key, q, limit,
        )
        try:
            result: SearchResult = await self._catalog.search(q=q, limit=limit)
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
            "handles={}",
            result.count, result.truncated, result.stale,
            [p.handle for p in result.results],
        )
        return json.dumps(
            {
                "query": result.query,
                "count": result.count,
                "truncated": result.truncated,
                "stale": result.stale,
                "manifest": asdict(result.manifest),
                "results": [_product_summary(p) for p in result.results],
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
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "price": price,
        "currency": currency,
        "in_stock": True,  # v1: asumimos True. Stock real-time es follow-up.
        "thumbnail_url": p.thumbnail,
        "tags": p.tags,
    }


def _product_full(p: CatalogProductDTO) -> dict[str, Any]:
    """Version completa para get_product_by_handle."""
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "description": p.description,
        "thumbnail": p.thumbnail,
        "variants": [
            {
                "id": v.id,
                "title": v.title,
                "sku": v.sku,
                "price": v.prices[0].amount if v.prices else None,
                "currency": v.prices[0].currency_code if v.prices else None,
            }
            for v in p.variants
        ],
        "images": [{"url": i.url, "rank": i.rank} for i in p.images],
        "tags": p.tags,
        "categories": p.categories,
    }


def _first_price(p: CatalogProductDTO) -> tuple[str | None, str | None]:
    if not p.variants:
        return (None, None)
    v = p.variants[0]
    if not v.prices:
        return (None, None)
    return (v.prices[0].amount, v.prices[0].currency_code)
