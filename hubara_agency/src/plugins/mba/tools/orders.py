"""Tools de pedidos del connector (lectura, canal 1).

- ``check_order_status``: pedidos del cliente que escribe. Base local del
  vault (``episodes[].order_id``, ``registered_order``, ``eta_tracking``) +
  enriquecimiento en vivo por el ``OrderQueryPort`` (etapa y ``pay_status``
  reales) con timeout corto; ante fallo degrada a lo local y lo dice.
- ``verify_order_for_checkout``: precio/stock en vivo por el
  ``CheckoutVerificationPort`` antes del resumen de confirmación.

Scoping: el teléfono que inyecta Meta es la única clave; un cliente jamás ve
pedidos de otra ``session_key``.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.sdk.connectorkit import CheckoutItem

_STAGE_LABELS: dict[str, str] = {
    "new": "recibido",
    "preparing": "en preparación",
    "ready": "listo para envío",
    "shipping": "en camino",
    "delivered": "entregado",
    "cancelled": "cancelado",
}
LIVE_TIMEOUT_S = 8.0
MAX_ORDERS = 5


def _fmt_when(at_ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(at_ms) / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except (TypeError, ValueError, OSError):
        return None


def _tracking_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict):
        return []
    orders = tracking.get("orders")
    if isinstance(orders, dict):
        return [v for v in orders.values() if isinstance(v, dict)]
    if tracking.get("order_id"):
        return [tracking]
    return []


def _metadata_order_ids(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    episodes = data.get("episodes")
    if isinstance(episodes, list):
        for ep in episodes:
            oid = ep.get("order_id") if isinstance(ep, dict) else None
            if isinstance(oid, str) and oid and oid not in ids:
                ids.append(oid)
    registered = data.get("registered_order")
    if isinstance(registered, dict):
        oid = registered.get("order_id")
        if isinstance(oid, str) and oid and oid not in ids:
            ids.append(oid)
    return ids


async def _live_summary(query_port: Any, order_id: str) -> Any | None:
    try:
        detail = await query_port.get(order_id)
    except Exception as exc:  # noqa: BLE001 — degradar a local, jamás reventar el endpoint
        logger.warning(
            "[mba] check_order_status live lookup {} falló: {}", order_id, exc
        )
        return None
    return getattr(detail, "summary", None) if detail is not None else None


async def _live_summaries(
    query_port: Any | None, order_ids: list[str]
) -> list[Any | None]:
    """Lookups en paralelo bajo UN presupuesto total (`LIVE_TIMEOUT_S`): el
    connector de Meta tiene su propio timeout y 5 × 8 s secuenciales lo vencerían."""
    if query_port is None or not order_ids:
        return [None] * len(order_ids)
    try:
        return await asyncio.wait_for(
            asyncio.gather(*(_live_summary(query_port, oid) for oid in order_ids)),
            timeout=LIVE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[mba] check_order_status: presupuesto de {}s agotado para {} pedidos",
            LIVE_TIMEOUT_S,
            len(order_ids),
        )
        return [None] * len(order_ids)


async def check_order_status(
    store: Any, query_port: Any | None, *, session_key: str
) -> dict[str, Any]:
    data = store.read(session_key)
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in _tracking_entries(data):
        oid = str(entry.get("order_id") or "")
        if not oid or oid in by_id:
            continue
        stage = str(entry.get("current_stage") or "new")
        events = entry.get("events") or []
        last = (
            events[-1]
            if isinstance(events, list) and events and isinstance(events[-1], dict)
            else {}
        )
        by_id[oid] = {
            "order_id": oid,
            "status": _STAGE_LABELS.get(stage, stage),
            "status_code": stage,
            "last_update": _fmt_when(last.get("at_ms") or entry.get("started_at_ms")),
        }
        order.append(oid)
    for oid in _metadata_order_ids(data):
        if oid not in by_id:
            by_id[oid] = {
                "order_id": oid,
                "status": "registrado",
                "status_code": None,
                "last_update": None,
            }
            order.append(oid)
    if not by_id:
        return {
            "orders": [],
            "note": "El cliente no tiene pedidos registrados en esta conversación.",
        }

    live_unavailable = False
    shown = order[:MAX_ORDERS]
    for oid, summary in zip(shown, await _live_summaries(query_port, shown)):
        if summary is None:
            live_unavailable = query_port is not None
            continue
        out = by_id[oid]
        stage = getattr(summary, "status", None)
        if isinstance(stage, str) and stage:
            out["status"] = _STAGE_LABELS.get(stage, stage)
            out["status_code"] = stage
        pay_status = getattr(summary, "pay_status", None)
        if isinstance(pay_status, str) and pay_status:
            out["pay_status"] = pay_status
            out["payment_confirmed"] = pay_status == "paid"
        display_id = getattr(summary, "display_id", None)
        if isinstance(display_id, str) and display_id:
            out["display_id"] = display_id
        total_cop = getattr(summary, "total_cop", None)
        if isinstance(total_cop, int):
            out["total_cop"] = total_cop
    payload: dict[str, Any] = {"orders": [by_id[oid] for oid in shown]}
    if live_unavailable:
        payload["note"] = (
            "No se pudo consultar el estado en vivo de todos los pedidos; los datos son los últimos conocidos. "
            "No afirmes que el pago está confirmado."
        )
    return payload


_CHECKOUT_DOWN = {
    "error": "catalog_unavailable",
    "message": (
        "No se pudo verificar el precio en vivo. Reintenta una vez; si se repite, pasa el caso a un colega "
        "con escalate_to_human (reason_category=CHECKOUT_VERIFY_FAILED)."
    ),
}


async def verify_order_for_checkout(
    verifier: Any | None, *, items: list[dict[str, Any]]
) -> dict[str, Any]:
    if verifier is None:
        return {**_CHECKOUT_DOWN, "detail": "checkout_not_configured"}
    bad = [it["handle"] for it in items if int(it["quantity"]) < 1]
    if bad:
        return {
            "error": "invalid_items",
            "message": "La cantidad de cada item debe ser 1 o más. Corrige y vuelve a verificar.",
            "items": bad,
        }
    parsed = [
        CheckoutItem(handle=str(it["handle"]), quantity=int(it["quantity"]))
        for it in items
    ]
    result = await verifier.verify_items(parsed)
    if not result.catalog_available:
        # el texto del vendor (host de Medusa, stack) va al log, no al agente
        logger.warning(
            "[mba] verify_order_for_checkout catalog_unavailable: {}",
            result.error_detail,
        )
        return {**_CHECKOUT_DOWN, "detail": "checkout_unavailable"}
    any_discrepancy = any(vi.discrepancy for vi in result.items)
    envelope: dict[str, Any] = {
        "verified": result.verified,
        "discrepancy": any_discrepancy,
        "items": [asdict(vi) for vi in result.items],
    }
    if any_discrepancy:
        envelope["message"] = (
            "Hay diferencias de precio o disponibilidad respecto a lo mostrado. Comunícale al cliente cada "
            "discrepancia (producto + precio anterior → precio actualizado) y pídele confirmación con el precio "
            "nuevo antes de registrar el pedido."
        )
    else:
        envelope["message"] = (
            "Verificación OK: precios y disponibilidad coinciden. Muestra el resumen para confirmar."
        )
    return envelope
