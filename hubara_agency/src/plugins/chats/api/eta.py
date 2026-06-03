"""HTTP endpoints del plugin frontend `eta` — sirviéndose desde `chats`.

El plugin `eta` es frontend-only (visualización de pedidos en seguimiento). Sus
datos viven en dos lugares, ambos accesibles vía **platform ports** (R-DIP:
chats → platform, nunca chats → plugin sibling):

  * El **timeline** (mensajes que el Agente ETA envió + respuestas del cliente)
    vive en ``metadata.eta_tracking`` de cada sesión del vault — lo escribe el
    ``HubaraEtaSessionWorkflow`` (chats/eta).
  * Los **datos del pedido** (cliente, ciudad, total, tipo de pago, stage
    actual) vienen del order query port (Medusa).

Por eso el endpoint vive en `chats` (donde está el vault) y el frontend del
plugin `eta` consume ``/api/chats/eta/*`` — mismo patrón que `ads`
(``chats/api/ads.py``).

Endpoints:
  GET /api/chats/eta/tracked-orders        → lista de pedidos en seguimiento.
  GET /api/chats/eta/tracked-orders/{id}   → uno (por display_id, ej. "#1247").
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path as PathParam

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.orders.composition import get_order_query_port

router = APIRouter()
log = logging.getLogger(__name__)

_BOGOTA = ZoneInfo("America/Bogota")

# Backend OrderStage → TrackedStage del frontend. El backend NO tiene `out`
# (En reparto); el frontend lo define pero queda sin uso (decisión HU ETA v1).
# `new` y `cancelled` no se muestran como "en seguimiento".
_STAGE_MAP: dict[str, str] = {
    "preparing": "preparing",
    "ready": "ready",
    "shipping": "shipping",
    "delivered": "delivered",
}

_COLORS = ("a", "b", "c", "d", "e", "f")


def _color_for(seed: str) -> str:
    """Color a..f estable derivado del id (fallback cuando no hay detalle Medusa)."""
    return _COLORS[sum(ord(c) for c in seed) % len(_COLORS)]


def _safe_query_port():
    """Order query port, o ``None`` si Medusa no está configurado.

    ``get_order_query_port()`` levanta cuando ``MEDUSA_BASE_URL`` está ausente
    (construye ``MedusaSettings()`` con base_url required antes del check de
    EmptyOrderQuery). El dashboard ETA NO debe 500: sin datos de pedido igual
    sirve el timeline (mensajes enviados + respuestas) desde ``eta_tracking``,
    que es el valor del seguimiento. Mismo espíritu que el contrato del kanban
    de orders ("Medusa no configurado → vacío, no error").
    """
    try:
        return get_order_query_port()
    except Exception:  # noqa: BLE001 — Medusa no configurado / settings inválidos
        log.warning(
            "eta: order query port no disponible (Medusa sin configurar) — "
            "sirvo el timeline sin datos vivos del pedido"
        )
        return None


def _format_time(at_ms: int | None) -> str:
    if not at_ms:
        return ""
    return datetime.fromtimestamp(at_ms / 1000, _BOGOTA).strftime("%H:%M")


def _format_date(at_ms: int | None, *, now: datetime) -> str:
    if not at_ms:
        return ""
    dt = datetime.fromtimestamp(at_ms / 1000, _BOGOTA)
    delta_days = (now.date() - dt.date()).days
    if delta_days <= 0:
        return "hoy"
    if delta_days == 1:
        return "ayer"
    return dt.strftime("%d/%m")


def _event_to_tracked(ev: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """``eta_tracking.events[i]`` → shape ``TrackedEvent`` del frontend."""
    return {
        "stage": ev.get("stage"),
        "time": _format_time(ev.get("at_ms")),
        "date": _format_date(ev.get("at_ms"), now=now),
        "note": ev.get("note"),
        "agentMsg": ev.get("agent_msg") or "",
        "reply": ev.get("reply"),
        "flagged": bool(ev.get("flagged", False)),
        "flag": ev.get("flag"),
    }


def _iter_eta_sessions(vault_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    """Devuelve [(session_id, eta_tracking)] de las sesiones con seguimiento ETA."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not vault_dir.exists():
        return out
    try:
        entries = sorted(vault_dir.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith(WHATSAPP_SESSION_PREFIX):
            continue
        meta_file = entry / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        tracking = meta.get("eta_tracking")
        if isinstance(tracking, dict) and tracking.get("order_id"):
            out.append((entry.name, tracking))
    return out


def _build_tracked_order(
    session_id: str,
    tracking: dict[str, Any],
    detail: Any | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Compone un ``TrackedOrder`` desde el tracking (timeline) + el detalle Medusa.

    Devuelve ``None`` si el stage actual no es uno de los 4 que mostramos
    (``new`` aún no entró a seguimiento; ``cancelled`` es terminal).
    """
    order_id = str(tracking.get("order_id") or "")
    events_raw = tracking.get("events") or []
    events = [_event_to_tracked(e, now=now) for e in events_raw if isinstance(e, dict)]
    needs = any(e["flagged"] for e in events)

    if detail is not None:
        s = detail.summary
        backend_stage = s.status
        current = _STAGE_MAP.get(backend_stage)
        if current is None:
            return None  # new / cancelled → fuera del tablero de seguimiento
        return {
            "id": s.display_id,
            "customer": s.customer,
            "short": s.short,
            "color": s.color,
            "city": s.city or "",
            "current": current,
            "channel": s.channel,
            "needs": needs,
            "payType": s.pay_type,
            "total": s.total_cop,
            "messagesUnread": 0,
            "events": events,
        }

    # Sin detalle Medusa (stub / borrado / Medusa no configurado): mostramos el
    # timeline igual (es el valor del seguimiento) con datos mínimos.
    current = _STAGE_MAP.get(str(tracking.get("current_stage") or ""))
    if current is None:
        return None
    return {
        "id": order_id,
        "customer": "Cliente",
        "short": "—",
        "color": _color_for(order_id or session_id),
        "city": "",
        "current": current,
        "channel": "WhatsApp",
        "needs": needs,
        "payType": "confirmed",
        "total": 0,
        "messagesUnread": 0,
        "events": events,
    }


@router.get("/eta/tracked-orders")
async def list_tracked_orders() -> dict[str, Any]:
    """Lista de pedidos en seguimiento por el Agente ETA (para la sección ETA).

    Response: ``{"orders": [TrackedOrder, ...], "count": int}``.
    """
    now = datetime.now(_BOGOTA)
    sessions = _iter_eta_sessions(Path(WORKSPACE_VAULT_DIR))
    port = _safe_query_port()

    if port is None:
        details: list[Any] = [None] * len(sessions)
    else:
        details = await asyncio.gather(
            *(port.get(tracking["order_id"]) for _sid, tracking in sessions),
            return_exceptions=True,
        )

    orders: list[dict[str, Any]] = []
    for (session_id, tracking), detail in zip(sessions, details):
        resolved = detail if not isinstance(detail, BaseException) else None
        built = _build_tracked_order(session_id, tracking, resolved, now=now)
        if built is not None:
            orders.append(built)

    return {"orders": orders, "count": len(orders)}


@router.get("/eta/tracked-orders/{display_id}")
async def get_tracked_order(
    display_id: str = PathParam(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    """Un pedido en seguimiento por su ``display_id`` (ej. ``#1247`` o ``1247``)."""
    now = datetime.now(_BOGOTA)
    needle = display_id.lstrip("#")
    sessions = _iter_eta_sessions(Path(WORKSPACE_VAULT_DIR))
    port = _safe_query_port()

    for session_id, tracking in sessions:
        detail = None
        if port is not None:
            try:
                detail = await port.get(tracking["order_id"])
            except Exception:  # noqa: BLE001 — best-effort por pedido
                detail = None
        built = _build_tracked_order(session_id, tracking, detail, now=now)
        if built is not None and built["id"].lstrip("#") == needle:
            return built

    raise HTTPException(status_code=404, detail=f"Tracked order {display_id!r} not found.")
