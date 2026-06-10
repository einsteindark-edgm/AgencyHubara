"""CAST chats→orders (order@v1 → order-ref) — canal 3 del PLUGIN_CONTRACT §5.3.

El canvas de pago del chat ("Confirmar pago" en modo intervenido) necesita dos
comandos del dominio de ÓRDENES: agendar entrega (draft→Order, new→preparing)
y confirmar pago. Pre-F4, el frontend de chats llamaba `/api/orders/*` directo
vía la entity central `order` — consumo cross-plugin lavado (hallazgo N-3/F8).

Este módulo es el **cast declarado** en el manifest de chats::

    depends_on: [orders]
    consumes:
      - { provider: orders, contract: order@v1, into: order-ref,
          cast: api/order_actions }

Reglas del cast (por qué está hecho así):

* Consume el **contrato HTTP publicado** del provider (`order@v1` =
  `PATCH /api/orders/orders/{id}/{schedule,confirm-payment}`) en vez del
  command-port de platform: el endpoint del provider encapsula efectos que le
  pertenecen (p.ej. `schedule` EMITE `OrderStageChangedEvent` → arranca el
  Agente ETA). Duplicar eso acá violaría la ownership del evento (P-3 además
  prohíbe importar `orders.*`).
* **Swap multitenant**: cambiar el provider (otro `orders` con otro contrato)
  = ajustar SOLO este módulo; el frontend de chats (entity local `order-ref`)
  queda intacto. Este archivo es el único punto de acoplamiento.
* `depends_on: [orders]` es la dep DURA que P-6 valida al boot: chats sin
  orders no arranca (el canvas de pago quedaría roto en silencio).
* Self-call de loopback: por defecto `http://127.0.0.1:8000` (la MISMA app
  FastAPI sirve ambos routers — async, sin deadlock). IPv4 explícito para
  evitar la carrera localhost IPv4/::1. Override: `ORDERS_API_BASE` (p.ej.
  si orders viviera en otro servicio).
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Path

router = APIRouter()

_TIMEOUT_S = 15.0


def _orders_base() -> str:
    return os.environ.get("ORDERS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


async def _forward_patch(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """PATCH al contrato publicado de orders; errores → HTTP claros del cast."""
    url = f"{_orders_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.patch(url, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"cast chats→orders: el provider no respondió ({exc.__class__.__name__}). "
                f"¿`orders` está habilitado y sirviendo? (depends_on lo exige al boot)"
            ),
        ) from exc
    if resp.status_code >= 400:
        # Passthrough del error del provider (422 de validación, etc.).
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502, detail="cast chats→orders: respuesta no-dict del provider"
        )
    return data


@router.patch("/order-actions/{order_id}/schedule")
async def schedule_order_for_chat(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Agendar entrega (draft→Order + new→preparing) vía el contrato order@v1.

    Body: `{delivery_iso: "YYYY-MM-DD", delivery_time?: "HH:MM", note?: str}`.
    Response: el `OrderCommandResult` plano del provider
    (`{success, order_id, current_stage, error_detail, audit_id}`).
    """
    return await _forward_patch(f"/api/orders/orders/{order_id}/schedule", body)


@router.patch("/order-actions/{order_id}/confirm-payment")
async def confirm_order_payment_for_chat(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Confirmar pago del pedido vía el contrato order@v1 (idempotente)."""
    return await _forward_patch(
        f"/api/orders/orders/{order_id}/confirm-payment", body
    )
