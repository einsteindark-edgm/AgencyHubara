"""Despacho de una ``ToolCall`` validada a su implementación.

Las 5 tools de lectura corren por canal 1 (ports del SDK). Las 4 de ESCRITURA
(set_order_slot, register_order, manage_conversation_tag, escalate_to_human)
delegan por cast (``deps.chats``, canal 3) al contrato ``session-actions@v1``
de chats — por eso reciben el ``request`` entrante (el castkit lo exige).
``run_tool`` devuelve ``None`` solo para una tool declarada sin lógica (501).
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from src.plugins.mba.domain.tool_calls import ToolCall
from src.plugins.mba.tools import catalog, orders, session
from src.plugins.mba.tools.deps import ToolDeps, default_deps

__all__ = ["ToolDeps", "default_deps", "run_tool"]

_WRITE_TOOLS = {
    "set_order_slot": session.set_order_slot,
    "register_order": session.register_order,
    "manage_conversation_tag": session.manage_conversation_tag,
    "escalate_to_human": session.escalate_to_human,
}

_CHATS_UNAVAILABLE = {
    "error": "chats_unavailable",
    "applied": False,
    "message": "La operación NO se aplicó (servicio no disponible). Pasa el caso a un colega.",
}


async def run_tool(call: ToolCall, deps: ToolDeps, request: Request | None = None) -> dict[str, Any] | None:
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
    write = _WRITE_TOOLS.get(call.tool)
    if write is not None:
        if deps.chats is None:
            return dict(_CHATS_UNAVAILABLE)
        return await write(deps.chats, request, session_key=call.session_key, params=p)
    return None
