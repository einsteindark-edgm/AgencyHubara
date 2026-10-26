"""`read_reply_window`: el turno de ventas espera a que vuelva el LLM
(incidente 2026-10-15) y pregunta si todavía le toca responder."""
from __future__ import annotations

import json
from typing import Any

from temporalio import activity

from src.plugins.chats.agent.sales.contracts import ReplyWindow
from src.sdk.runtime import WORKSPACE_VAULT_DIR

_ROUTE_VENTAS = "ventas"


def _read(session_id: str) -> dict[str, Any]:
    """La metadata del chat; {} si no existe o no se puede leer (sin ella el
    chat sigue siendo del bot y la ventana no se conoce: el turno espera hasta
    su tope)."""
    try:
        data = json.loads((WORKSPACE_VAULT_DIR / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@activity.defn(name="read_reply_window")
async def read_reply_window_activity(session_id: str) -> ReplyWindow:
    """Quién tiene el chat (`active_route`) y cuándo cierra la ventana de 24 h
    (`service_window_expires_at_ms`, que el ingest mueve con cada mensaje del
    cliente, también con los que llegaron mientras el turno esperaba)."""
    data = _read(session_id)
    route = data.get("active_route")
    expires = data.get("service_window_expires_at_ms")
    return ReplyWindow(
        route=route if isinstance(route, str) and route else _ROUTE_VENTAS,
        window_expires_at_ms=expires if isinstance(expires, int) and not isinstance(expires, bool) else None,
    )
