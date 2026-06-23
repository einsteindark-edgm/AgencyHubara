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
* **Timeout dimensionado por el UPSTREAM del provider, no por el hop local**
  (lección L-1): orders habla con Medusa cloud — 30s/request × 3 retries
  tenacity, y `schedule` encadena varias llamadas. Default 120s, override
  `ORDERS_CAST_TIMEOUT_S`.
* **Timeout ≠ "no pasó nada"**: si el provider no respondió a tiempo, el
  comando PUEDE haberse aplicado igual (el request interno sigue/avanzó
  server-side; el reconcile converge el estado). Eso se reporta 504 con
  mensaje honesto. Solo un fallo de CONEXIÓN garantiza no-aplicación → 502.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Path, Request

from src.sdk import castkit

router = APIRouter()

# Peor caso de UNA llamada Medusa del provider: 30s × 3 retries tenacity
# (+ backoff) ≈ 95s; `schedule` encadena varias. 15s (el valor original,
# pensado para loopback) abortaba comandos que SÍ se aplicaban (L-1).
_DEFAULT_TIMEOUT_S = 120.0


def _timeout_s() -> float:
    return float(os.environ.get("ORDERS_CAST_TIMEOUT_S", _DEFAULT_TIMEOUT_S))


def _orders_base() -> str:
    return os.environ.get("ORDERS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


_CAST_LABEL = "chats→orders"


async def _forward_patch(
    request: Request, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    """PATCH al contrato publicado de orders, portando la identidad del edge.

    La mecánica del cast la centraliza ``src.sdk.castkit`` (Canal 3):
    propagación del ``Authorization`` (sin esto, el 2º hop bajo ``require_auth``
    daba 401) + semántica honesta de fallos — connect → 502 "NO se aplicó",
    timeout → 504 "PUEDE haberse aplicado" (L-1) — + ``detail`` desanidado. El
    timeout se dimensiona por el UPSTREAM del provider (Medusa cloud), no por el
    hop local (L-1); por eso lo pasamos explícito.
    """
    return await castkit.forward(
        request,
        "PATCH",
        path,
        base_url=_orders_base(),
        timeout=_timeout_s(),
        cast_label=_CAST_LABEL,
        body=body,
    )


@router.patch("/order-actions/{order_id}/schedule")
async def schedule_order_for_chat(
    request: Request,
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Agendar entrega (draft→Order + new→preparing) vía el contrato order@v1.

    Body: `{delivery_iso: "YYYY-MM-DD", delivery_time?: "HH:MM", note?: str}`.
    Response: el `OrderCommandResult` plano del provider
    (`{success, order_id, current_stage, error_detail, audit_id}`).
    """
    return await _forward_patch(
        request, f"/api/orders/orders/{order_id}/schedule", body
    )


@router.patch("/order-actions/{order_id}/confirm-payment")
async def confirm_order_payment_for_chat(
    request: Request,
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Confirmar pago del pedido vía el contrato order@v1 (idempotente)."""
    return await _forward_patch(
        request, f"/api/orders/orders/{order_id}/confirm-payment", body
    )
