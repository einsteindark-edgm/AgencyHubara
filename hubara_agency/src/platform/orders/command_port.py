"""`OrderCommandPort` — write-side abstraction para mutar órdenes desde el
dashboard humano.

Espejo del read-side `OrderQueryPort`. Las operaciones que el operador realiza
desde el inspector + kanban (agendar, transicionar stage, confirmar pago,
cancelar) cruzan este port hacia el adapter Medusa.

R-DIP: el Protocol vive en platform; los consumers son los routers del plugin
`orders` (FastAPI). Implementaciones (`MedusaOrderCommand`) viven en el mismo
package y se cablean via composition root.

R-JSON: DTOs in/out son `@dataclass(frozen=True)` JSON-safe. Errores se
transportan en `OrderCommandResult.error_detail` (string), no como excepciones
— así el caller HTTP traduce a status code apropiado.

Operaciones cubiertas:
  * `schedule_delivery`: humano agenda la entrega + nota → patch metadata +
    transition new → preparing.
  * `transition_stage`: drag-and-drop entre columnas o click "marcar lista".
  * `confirm_payment`: humano click "Confirmar pago" cuando el cliente
    transfirió / pagó COD.
  * `cancel_order`: humano cancela (cliente arrepintió, error operativo).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.platform.orders.state import OrderStage


@dataclass(frozen=True)
class ScheduleDeliveryCommand:
    """Agendar la entrega + transicionar a `preparing`.

    `delivery_iso`: YYYY-MM-DD. El frontend valida formato; el adapter lo
    pasa raw a metadata.

    `delivery_time`: HH:MM opcional. Si None, el frontend muestra "—".

    `note`: texto libre del humano (instrucciones especiales, contexto).
    Persiste como `hubara_human_note`.
    """
    order_id: str
    delivery_iso: str
    delivery_time: str | None
    note: str | None
    by: str = "human"


@dataclass(frozen=True)
class TransitionStageCommand:
    """Cambiar el stage operacional. Drag-and-drop o click directo.

    `force`: bypassa el DAG (útil para correcciones del humano, ej. mover
    de `ready` de vuelta a `preparing` si el operador clickeó por error).
    El frontend NO setea `force=True` por default — solo si pasa por un
    confirm dialog explícito.
    """
    order_id: str
    to_stage: OrderStage
    note: str | None = None
    force: bool = False
    by: str = "human"


@dataclass(frozen=True)
class ConfirmPaymentCommand:
    """Marcar `hubara_payment_confirmed=True` en metadata.

    En el futuro (gateway integrado) esto también dispararía un capture
    en Medusa. Hoy es solo flag para que el frontend muestre el badge
    "Pago confirmado".
    """
    order_id: str
    by: str = "human"


@dataclass(frozen=True)
class CancelOrderCommand:
    """Cancelar la orden. Transición a `cancelled` con `force=True` porque
    el destino es alcanzable desde cualquier estado no terminal.

    `reason`: motivo de cancelación. Persiste como `hubara_cancelled_reason`
    y aparece en el inspector. Si None, queda "Cancelada por operador".
    """
    order_id: str
    reason: str | None = None
    by: str = "human"


@dataclass(frozen=True)
class OrderCommandResult:
    """Resultado uniforme de cualquier comando.

    `success`: True si Medusa aceptó el patch. False si:
      * Order no existe en Medusa (404).
      * Transición inválida (caller debe HTTP 409).
      * Medusa rechazó (5xx, config error).
      * Adapter timeout / network error.

    `current_stage`: el stage RESULTANTE tras aplicar el comando. Útil para
    optimistic updates del frontend (puede actualizar la UI sin re-fetch).

    `error_detail`: si success=False, mensaje human-readable.
      Formato: "kind: detail" — el frontend hace switch por `kind` para mostrar
      el dialog adecuado. Kinds canónicos:
        - "not_found: ..."
        - "invalid_transition: ..."
        - "medusa_unavailable: ..."
        - "medusa_api_error: HTTP <code> ..."
        - "validation_error: ..."

    `audit_id`: opcional. Si la operación NO pudo escribir en Medusa pero SÍ
    quedó persistida en vault local (futuro retry), este id permite al humano
    reconciliar.
    """
    success: bool
    order_id: str
    current_stage: OrderStage | None = None
    error_detail: str | None = None
    audit_id: str | None = None


@runtime_checkable
class OrderCommandPort(Protocol):
    """Contrato write-side para mutar órdenes.

    Implementaciones:
      * `MedusaOrderCommand` (live) — escribe a Medusa via `HttpMedusaClient`.
      * `NoopOrderCommand` (stub) — devuelve `success=False` siempre con
        detail explícito, para entornos sin Medusa configurado.
    """

    async def schedule_delivery(
        self, command: ScheduleDeliveryCommand
    ) -> OrderCommandResult: ...

    async def transition_stage(
        self, command: TransitionStageCommand
    ) -> OrderCommandResult: ...

    async def confirm_payment(
        self, command: ConfirmPaymentCommand
    ) -> OrderCommandResult: ...

    async def cancel_order(
        self, command: CancelOrderCommand
    ) -> OrderCommandResult: ...
