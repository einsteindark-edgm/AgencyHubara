"""HTTP endpoints del plugin frontend `eta` — sirviéndose desde `chats`.

El plugin `eta` es frontend-only (visualización de pedidos en seguimiento).

  * El **listado** se deriva del **order query port**: todos los pedidos en una
    etapa de fulfillment (``preparing``/``ready``/``shipping``/``delivered``) —
    la MISMA fuente que el kanban de orders. Así un pedido aparece en
    seguimiento apenas entra a preparación, exista o no todavía un timeline del
    agente (clave: los pedidos ya en fulfillment ANTES de que el agente
    existiera igual se muestran; el timeline se llena cuando el agente notifica).
  * Sobre cada pedido se **superpone el timeline** (mensajes que el Agente ETA
    envió + respuestas del cliente), que vive en ``metadata.eta_tracking`` de la
    sesión del vault — lo escribe el ``HubaraEtaSessionWorkflow`` (chats/eta).
    El match pedido↔timeline es por ``eta_tracking.order_id`` == el id Medusa.

Ambas fuentes son **platform ports / vault** (R-DIP: chats → platform, nunca
chats → plugin sibling). Por eso el endpoint vive en `chats` (donde está el
vault) y el frontend del plugin `eta` consume ``/api/chats/eta/*`` — mismo
patrón que `ads` (``chats/api/ads.py``).

Si Medusa no está configurado (dev sin .env / order list falla), el listado cae
a **timeline-only**: solo los pedidos que el agente ya trackeó, derivados de
``eta_tracking`` sin datos vivos del pedido.

Endpoints:
  GET /api/chats/eta/tracked-orders        → lista de pedidos en seguimiento.
  GET /api/chats/eta/tracked-orders/{id}   → uno (por display_id, ej. "#1247").
"""
from __future__ import annotations

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


# Límite del listado. Los pedidos "en seguimiento" son un subconjunto chico
# (solo fulfillment); 100 cubre el board sin paginar. Si se supera, se loguea
# (no truncado silencioso).
_LIST_LIMIT = 100


def _tracking_index(vault_dir: Path) -> dict[str, dict[str, Any]]:
    """``{order_id: eta_tracking}`` para superponer el timeline sobre cada pedido.

    Si dos sesiones trackean el mismo ``order_id`` (no debería pasar), gana la
    última — el board muestra un solo timeline por pedido.
    """
    out: dict[str, dict[str, Any]] = {}
    for _sid, tracking in _iter_eta_sessions(vault_dir):
        oid = str(tracking.get("order_id") or "")
        if oid:
            out[oid] = tracking
    return out


def _events_from_tracking(
    tracking: dict[str, Any] | None, *, now: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """``eta_tracking.events`` → eventos del frontend + flag ``needs`` (algún flagged)."""
    events_raw = (tracking or {}).get("events") or []
    events = [_event_to_tracked(e, now=now) for e in events_raw if isinstance(e, dict)]
    needs = any(e["flagged"] for e in events)
    return events, needs


def _tracked_from_summary(
    summary: Any,
    tracking: dict[str, Any] | None,
    *,
    current: str,
    now: datetime,
) -> dict[str, Any]:
    """``OrderSummaryDTO`` (datos vivos del pedido) + timeline → ``TrackedOrder``.

    El pedido se muestra SIEMPRE (viene del order port); el ``tracking`` puede
    ser ``None`` (el agente todavía no notificó este pedido) → timeline vacío.
    """
    events, needs = _events_from_tracking(tracking, now=now)
    return {
        "id": summary.display_id,
        "customer": summary.customer,
        "short": summary.short,
        "color": summary.color,
        "city": summary.city or "",
        "current": current,
        "channel": summary.channel,
        "needs": needs,
        "payType": summary.pay_type,
        "total": summary.total_cop,
        "messagesUnread": 0,
        "events": events,
    }


def _tracked_from_timeline(
    session_id: str, tracking: dict[str, Any], *, now: datetime
) -> dict[str, Any] | None:
    """Fallback timeline-only (Medusa caído / sin configurar): construye el
    ``TrackedOrder`` SOLO desde ``eta_tracking``, con datos mínimos del pedido.

    Devuelve ``None`` si el stage actual no es uno de los 4 visibles.
    """
    current = _STAGE_MAP.get(str(tracking.get("current_stage") or ""))
    if current is None:
        return None
    order_id = str(tracking.get("order_id") or "")
    events, needs = _events_from_tracking(tracking, now=now)
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


def _timeline_only(vault_dir: Path, *, now: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session_id, tracking in _iter_eta_sessions(vault_dir):
        built = _tracked_from_timeline(session_id, tracking, now=now)
        if built is not None:
            out.append(built)
    return out


async def _compose_tracked_orders(*, now: datetime) -> list[dict[str, Any]]:
    """Lista de ``TrackedOrder``: pedidos en fulfillment (order port) + timeline.

    Esta es la corrección del bug "la sección ETA no carga ninguna orden": el
    listado se deriva de los pedidos REALES en fulfillment, no de qué sesiones
    tienen ``eta_tracking``. Así los pedidos que ya estaban en preparación/envío
    antes de que el agente existiera igual aparecen (con timeline vacío hasta que
    el agente notifique).
    """
    vault = Path(WORKSPACE_VAULT_DIR)
    port = _safe_query_port()
    if port is None:
        return _timeline_only(vault, now=now)

    try:
        listing = await port.list(limit=_LIST_LIMIT, offset=0, include_drafts=True)
    except Exception:  # noqa: BLE001 — Medusa caído → servimos lo trackeado
        log.warning(
            "eta: order list falló — sirvo timeline-only desde eta_tracking",
            exc_info=True,
        )
        return _timeline_only(vault, now=now)

    if listing.count > _LIST_LIMIT:
        log.info(
            "eta: %d pedidos exceden el cap %d — el board muestra los primeros",
            listing.count, _LIST_LIMIT,
        )

    tracking_by_order = _tracking_index(vault)
    out: list[dict[str, Any]] = []
    for summary in listing.orders:
        current = _STAGE_MAP.get(summary.status)
        if current is None:
            continue  # new / cancelled → fuera del tablero de seguimiento
        out.append(
            _tracked_from_summary(
                summary, tracking_by_order.get(summary.id), current=current, now=now
            )
        )
    return out


@router.get("/eta/tracked-orders")
async def list_tracked_orders() -> dict[str, Any]:
    """Lista de pedidos en seguimiento por el Agente ETA (para la sección ETA).

    Pedidos en fulfillment (order port) con el timeline del agente superpuesto.
    Response: ``{"orders": [TrackedOrder, ...], "count": int}``.
    """
    orders = await _compose_tracked_orders(now=datetime.now(_BOGOTA))
    return {"orders": orders, "count": len(orders)}


@router.get("/eta/tracked-orders/{display_id}")
async def get_tracked_order(
    display_id: str = PathParam(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    """Un pedido en seguimiento por su ``display_id`` (ej. ``#1247`` o ``1247``)."""
    needle = display_id.lstrip("#")
    for built in await _compose_tracked_orders(now=datetime.now(_BOGOTA)):
        if built["id"].lstrip("#") == needle:
            return built

    raise HTTPException(status_code=404, detail=f"Tracked order {display_id!r} not found.")
