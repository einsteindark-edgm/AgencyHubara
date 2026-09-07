"""Despacho de una ``ToolCall`` validada a su implementación.

``run_tool`` devuelve el dict de respuesta, o ``None`` si la tool todavía no
tiene lógica (el endpoint responde 501: contrato registrado, lógica pendiente).
Las 4 tools de escritura (set_order_slot, register_order,
manage_conversation_tag, escalate_to_human) delegan en use cases que posee
``chats`` (draft, episodios, etiquetas) y llegan por cast en el siguiente PR
de D1.2.
"""
from __future__ import annotations

from typing import Any

from src.plugins.mba.domain.tool_calls import ToolCall
from src.plugins.mba.tools import catalog, orders
from src.plugins.mba.tools.deps import ToolDeps, default_deps

__all__ = ["ToolDeps", "default_deps", "run_tool"]


async def run_tool(call: ToolCall, deps: ToolDeps) -> dict[str, Any] | None:
    p = call.params
    if call.tool == "search_products":
        return await catalog.search_products(
            deps.catalog, q=p.get("q", ""), category=p.get("category"), limit=p.get("limit", catalog.DEFAULT_LIMIT)
        )
    if call.tool == "list_categories":
        return await catalog.list_categories(deps.catalog)
    if call.tool == "get_product_by_handle":
        return await catalog.get_product_by_handle(deps.catalog, handle=p["handle"])
    if call.tool == "check_order_status":
        return await orders.check_order_status(deps.metadata, deps.order_query, session_key=call.session_key)
    if call.tool == "verify_order_for_checkout":
        return await orders.verify_order_for_checkout(deps.checkout, items=p["items"])
    return None
