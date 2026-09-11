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
from loguru import logger

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


async def run_tool(
    call: ToolCall, deps: ToolDeps, request: Request | None = None
) -> dict[str, Any] | None:
    p = call.params
    if call.tool == "search_products":
        return await catalog.search_products(
            deps.catalog,
            q=p.get("q", ""),
            category=p.get("category"),
            limit=p.get("limit", catalog.DEFAULT_LIMIT),
        )
    if call.tool == "list_categories":
        return await catalog.list_categories(deps.catalog)
    if call.tool == "get_product_by_handle":
        return await catalog.get_product_by_handle(deps.catalog, handle=p["handle"])
    if call.tool == "check_order_status":
        return await orders.check_order_status(
            deps.metadata, deps.order_query, session_key=call.session_key
        )
    if call.tool == "verify_order_for_checkout":
        return await orders.verify_order_for_checkout(deps.checkout, items=p["items"])
    write = _WRITE_TOOLS.get(call.tool)
    if write is not None:
        if deps.chats is None:
            return dict(_CHATS_UNAVAILABLE)
        result = await write(
            deps.chats, request, session_key=call.session_key, params=p
        )
        await _episode_boundary(deps, call.session_key, result)
        return result
    return None


async def _episode_boundary(
    deps: ToolDeps, session_key: str, result: dict[str, Any]
) -> None:
    """D1.10: si chats cerró el episodio (``episode_closed`` en la respuesta
    del contrato), MBA recibe la nota de frontera. Best-effort: nunca altera
    ni retrasa (la implementación real corre en background) el envelope."""
    if not isinstance(result, dict):
        return
    # ``register_order`` arma su propio envelope y deja el cierre en una clave
    # privada (no viaja a Meta); ``manage_conversation_tag`` pasa el contrato tal cual.
    closed = result.pop("_episode_closed", None) or result.get("episode_closed")
    if (
        deps.episode_boundary is None
        or not isinstance(closed, dict)
        or not closed.get("episode_id")
    ):
        return
    try:
        await deps.episode_boundary(
            session_key,
            closing_tag=str(closed.get("closing_tag") or ""),
            episode_id=str(closed["episode_id"]),
            order_id=(str(result["order_id"]) if result.get("order_id") else None),
            # el texto para MBA lleva la referencia legible (#22), no el id crudo de Medusa
            order_reference=(
                str(result.get("order_reference") or result.get("order_id") or "")
                or None
            ),
        )
    except Exception as exc:  # noqa: BLE001 — la tool ya se aplicó; la nota es best-effort
        logger.warning("[mba] episode_boundary falló session={}: {}", session_key, exc)
