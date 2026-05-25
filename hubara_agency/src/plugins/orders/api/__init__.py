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

from fastapi import APIRouter, HTTPException, Path, Query

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.orders.composition import get_order_query_port
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
