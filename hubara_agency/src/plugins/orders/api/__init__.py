"""HTTP API del plugin `orders`.

Expone:
  GET  /api/orders/orders              → lista de órdenes (kanban).
  GET  /api/orders/orders/{id}         → detalle de una orden (inspector).
  GET  /api/orders/orders-health       → sanity check del port + Medusa.
  GET  /api/orders/vault-orders        → órdenes que NO están en Medusa
                                          (failed + stub) para que el
                                          operador las reconcilie manualmente.

Fuente de la verdad: Medusa v2 (`/admin/orders` + `/admin/draft-orders`)
via `OrderQueryPort` (`platform/orders/query_port.py`). Si Medusa no esta
configurado, los endpoints devuelven shape valido pero vacio con un flag
`catalog_available=False` para que el frontend pinte un estado vacio
explicito (no error 500).

Datos faltantes (slots que Medusa no tiene todavia: due_date, agent
assignee, timeline detallado, notas, customer history) se devuelven con un
array `data_completeness_missing[]` y el frontend pinta un marker
"Datos pendientes de integración" sobre esos campos.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Path, Query

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.orders.command_port import (
    CancelOrderCommand,
    ConfirmPaymentCommand,
    ScheduleDeliveryCommand,
    TransitionStageCommand,
)
from src.platform.orders.composition import (
    get_order_command_port,
    get_order_query_port,
)
from src.platform.orders.state import STAGE_VALUES
from src.plugins.orders.vault_scanner import scan_vault_orders

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/orders")
async def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_drafts: bool = Query(
        default=True,
        description=(
            "Si True, fusiona Draft Orders (pedidos recien cerrados via "
            "register_order, pero no completados) con Orders. Default True."
        ),
    ),
) -> dict[str, Any]:
    """Lista de ordenes para el kanban del dashboard.

    Response shape:
      {
        "orders": [OrderSummaryDTO, ...],
        "count": int,
        "offset": int,
        "limit": int,
        "catalog_available": bool,
        "error_detail": str | null
      }

    `catalog_available=false` significa que Medusa no esta configurado /
    fallo — los campos `orders=[]` quedan vacios. El frontend muestra un
    estado vacio en lugar de error.
    """
    port = get_order_query_port()
    result = await port.list(
        limit=limit, offset=offset, include_drafts=include_drafts
    )
    return {
        "orders": [asdict(o) for o in result.orders],
        "count": result.count,
        "offset": result.offset,
        "limit": result.limit,
        "catalog_available": result.catalog_available,
        "error_detail": result.error_detail,
    }


@router.get("/orders/{order_id}")
async def get_order_detail(
    order_id: str = Path(
        ...,
        description=(
            "ID de la orden o draft_order de Medusa (formato "
            "'order_01HXX...' o 'draft_01HXX...'). El endpoint hace "
            "fallback automatico entre ambos si el prefix no matchea."
        ),
        min_length=1,
        max_length=200,
    ),
) -> dict[str, Any]:
    """Detalle de una orden para el panel inspector.

    Response shape: el OrderDetailDTO serializado (incluye summary,
    items_detail, addresses, totales, timeline minimo, payment_method_label,
    y `data_completeness_missing[]` con los slots que la UI debe marcar
    como "Datos pendientes de integración").

    Returns 404 si Medusa no encuentra la orden por id (probado tanto
    `/admin/orders/{id}` como `/admin/draft-orders/{id}`).
    """
    port = get_order_query_port()
    detail = await port.get(order_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Order {order_id!r} not found in Medusa.",
        )
    return asdict(detail)


@router.get("/vault-orders")
async def get_vault_orders() -> dict[str, Any]:
    """Premortem F2+K1: lista pedidos que existen en el vault local pero NO
    en Medusa. Dos casos:

    1. `kind="failed"`: el agente Sales llamó `register_order` pero Medusa
       rechazó (5xx, network down, config rota). El payload completo
       quedó en `metadata.failed_order_registrations[]` para que el
       operador lo registre manualmente.
    2. `kind="stub"`: el agente Sales registró el pedido con
       `StubOrderRegistration` (porque Medusa no estaba configurado al
       momento del cierre). El cliente recibió confirmación pero NO existe
       en Medusa hasta que alguien lo migre.

    Sin este endpoint, esos pedidos serían INVISIBLES en el dashboard —
    el operador podria perder ventas silenciosamente.

    Response shape:
      {
        "records": [
          {
            "kind": "failed" | "stub",
            "session_key": "wa_57311...",
            "order_id": "AUDIT-..." | "HUB-...",
            "customer_phone": "+57...",
            "customer_city": "Bogotá",
            "total_cop": 17000,
            "currency": "COP",
            "items_count": 2,
            "payment_method": "transfer",
            "error_detail": "medusa_api_error: HTTP 503" | null,
            "registered_at_ms": 1779800400000,
            "raw": {...}
          }
        ],
        "count": N,
        "failed_count": N1,
        "stub_count": N2
      }
    """
    records = scan_vault_orders(WORKSPACE_VAULT_DIR)
    failed = [r for r in records if r.kind == "failed"]
    stub = [r for r in records if r.kind == "stub"]
    return {
        "records": [asdict(r) for r in records],
        "count": len(records),
        "failed_count": len(failed),
        "stub_count": len(stub),
    }


@router.patch("/orders/{order_id}/schedule")
async def schedule_order(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(
        ...,
        examples=[
            {
                "delivery_iso": "2026-05-26",
                "delivery_time": "09:00",
                "note": "Cliente prefiere antes de las 10am",
            }
        ],
    ),
) -> dict[str, Any]:
    """Agendar entrega + transicionar `new → preparing`.

    Body:
      * `delivery_iso` (required) — YYYY-MM-DD
      * `delivery_time` (optional) — HH:MM
      * `note` (optional) — texto del humano

    Response: `{success, current_stage, error_detail?}` plano.
      * HTTP 200 con success=True → frontend invalidate query list+detail.
      * HTTP 200 con success=False → frontend muestra el error_detail.
      * HTTP 4xx solo si validación del body falla.
    """
    delivery_iso = body.get("delivery_iso")
    if not isinstance(delivery_iso, str) or not delivery_iso:
        raise HTTPException(
            status_code=422,
            detail="`delivery_iso` (YYYY-MM-DD) es requerido",
        )
    cmd = ScheduleDeliveryCommand(
        order_id=order_id,
        delivery_iso=delivery_iso,
        delivery_time=body.get("delivery_time")
        if isinstance(body.get("delivery_time"), str)
        else None,
        note=body.get("note")
        if isinstance(body.get("note"), str)
        else None,
    )
    port = get_order_command_port()
    result = await port.schedule_delivery(cmd)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/stage")
async def transition_order_stage(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(
        ...,
        examples=[{"stage": "ready", "note": "Empaquetado"}],
    ),
) -> dict[str, Any]:
    """Drag-and-drop o click directo. Valida transición permitida.

    Body:
      * `stage` (required) — uno de: new, preparing, ready, shipping,
        delivered, cancelled.
      * `note` (optional)
      * `force` (optional bool, default False) — bypassa DAG. Usar solo
        para correcciones explícitas (UI debe pedir confirm dialog).

    Response shape igual que `/schedule`. `success=False` con
    `error_detail` que empieza con `invalid_transition:` cuando el
    movimiento no es permitido (frontend muestra dialog explicativo).
    """
    stage_raw = body.get("stage")
    if stage_raw not in STAGE_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"`stage` debe ser uno de {sorted(STAGE_VALUES)}, "
                f"recibido {stage_raw!r}"
            ),
        )
    cmd = TransitionStageCommand(
        order_id=order_id,
        to_stage=stage_raw,  # type: ignore[arg-type]
        note=body.get("note")
        if isinstance(body.get("note"), str)
        else None,
        force=bool(body.get("force", False)),
    )
    port = get_order_command_port()
    result = await port.transition_stage(cmd)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/confirm-payment")
async def confirm_order_payment(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Marcar pago como confirmado (manual del humano).

    Body opcional `{"by": "string"}` para auditoría.

    Hoy esto NO toca el `payment_status` real de Medusa (sin gateway
    integrado). Solo escribe `hubara_payment_confirmed=True` en metadata.
    Cuando integremos gateway, este endpoint también capturará el pago en
    Medusa.

    Idempotente: si ya estaba confirmado, devuelve success=True sin
    side effects.
    """
    by = body.get("by") if isinstance(body.get("by"), str) else "human"
    cmd = ConfirmPaymentCommand(order_id=order_id, by=by)
    port = get_order_command_port()
    result = await port.confirm_payment(cmd)
    return _serialize_command_result(result)


@router.post("/orders/{order_id}/cancel")
async def cancel_order_endpoint(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Cancelar la orden. Transición a `cancelled` con `force=True`.

    Body opcional `{"reason": "texto"}` — persiste como
    `hubara_cancelled_reason` y aparece en el inspector + timeline.

    Idempotente si ya estaba cancelada (devuelve success=True).
    """
    reason = (
        body.get("reason")
        if isinstance(body.get("reason"), str)
        else None
    )
    cmd = CancelOrderCommand(order_id=order_id, reason=reason)
    port = get_order_command_port()
    result = await port.cancel_order(cmd)
    return _serialize_command_result(result)


def _serialize_command_result(result) -> dict[str, Any]:
    """Shape común para los 4 endpoints write-side."""
    return {
        "success": result.success,
        "order_id": result.order_id,
        "current_stage": result.current_stage,
        "error_detail": result.error_detail,
        "audit_id": result.audit_id,
    }


@router.get("/orders-health")
async def orders_health() -> dict[str, Any]:
    """Sanity-check del port — util para verificar config en prod sin
    consumir cuota de Medusa. Devuelve qué tipo de port esta inyectado
    y si Medusa responde en una llamada vacia."""
    port = get_order_query_port()
    port_name = type(port).__name__
    # Intentar una list vacia — captura errores de auth / network sin
    # consumir mucho (limit=1).
    probe = await port.list(limit=1, offset=0, include_drafts=False)
    return {
        "port": port_name,
        "catalog_available": probe.catalog_available,
        "error_detail": probe.error_detail,
        "sample_count": probe.count,
    }
