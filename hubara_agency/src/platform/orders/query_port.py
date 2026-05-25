"""OrderQueryPort — abstraccion para consultar ordenes desde la fuente de
la verdad (Medusa v2) y servirlas al dashboard frontend.

R-DIP: el Protocol vive aqui; los consumers (los routers FastAPI del plugin
`orders` en `src/plugins/orders/api/`) reciben una instancia via composition
root (`src/platform/orders/composition.py`).

R-JSON: los DTOs son `@dataclass(frozen=True)` planos, JSON-safe (string
amounts en COP por consistencia con el catalog snapshot; no Decimal, no
datetime).

Diseño:
  * El frontend Dashboard kanban actual (mock data en `entities/order/api.ts`)
    espera un shape MUY especifico (`OrderStatus` union, `payStatus`, etc.).
  * Este port hace el trabajo de TRADUCCION: Medusa expone
    `payment_status` + `fulfillment_status` (enums separados), y el frontend
    los quiere fusionados en `status` + `payStatus`. La fuente de la verdad
    es Medusa, pero la presentacion es ours.
  * Campos que Medusa NO tiene (due date, agent assignee, priority,
    timeline detallado, notas internas, customer history) se devuelven con
    `data_completeness` listando los slots ausentes — el frontend pinta un
    marker visual ("Datos pendientes de integración").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# Status enum que SI mapea 1-a-1 al frontend Dashboard.
# Notar que "delayed" es una derivacion client-side (overdue + status no
# terminal), asi que el adapter nunca lo emite.
OrderUiStatus = Literal[
    "new", "preparing", "ready", "shipping", "delivered", "cancelled",
]
OrderPayStatus = Literal["paid", "partial", "pending", "refund"]
OrderPayType = Literal["cod", "confirmed"]


@dataclass(frozen=True)
class OrderItemDTO:
    """Item del pedido (line item). Snapshot del estado al cierre de venta."""
    title: str
    sku: str | None
    quantity: int
    unit_price_cop: int
    total_cop: int
    variant_label: str | None = None
    thumbnail: str | None = None
    handle: str | None = None  # producto handle si esta en metadata


@dataclass(frozen=True)
class OrderAddressDTO:
    """Direccion de envio o facturacion. Todos campos opcionales — Medusa
    los acepta vacios y el frontend muestra '—' si faltan."""
    first_name: str | None
    last_name: str | None
    phone: str | None
    address_1: str | None
    address_2: str | None  # neighborhood en nuestra convencion
    city: str | None
    country_code: str | None


@dataclass(frozen=True)
class OrderTimelineEventDTO:
    """Evento del lifecycle del pedido. Para v1, solo el created/cancelled
    + el primer captured_at (si hay transactions). Futuro: leer
    `order_changes` para historia completa."""
    type: str          # "created" | "payment_captured" | "cancelled" | "fulfilled"
    label: str         # "Pedido creado" | "Pago capturado" | etc.
    timestamp_ms: int
    detail: str | None = None


@dataclass(frozen=True)
class OrderSummaryDTO:
    """Shape leve para la lista del kanban — corresponde 1-a-1 al
    `Order` interface del frontend (`entities/order/model.ts`).

    Campos derivados/sintetizados aqui:
      * `short`: primeras 2 letras de customer name. Si no hay nombre, "—".
      * `color`: hash deterministico del id (a..f).
      * `due` / `dueIso` / `dueTime`: NO los tenemos en Medusa todavia.
         Por ahora `dueIso` = `created_at + 1 dia` (estimate operacional)
         y `dueTime` = "—". Se marcaran en `data_completeness=['due_date']`.
      * `overdue`: derivado client-side de `dueIso < today`.
      * `priority`: derivado de `total > 200000 COP → "alta"`, sino "normal".
      * `agent`: leido de `metadata.agent` (el LLM no lo setea hoy), default "—".
      * `channel`: leido de `metadata.source` ("hubara_whatsapp_sales" → "WhatsApp"),
                   sino "Web" por default Medusa.
    """
    id: str                # Medusa id (e.g. "order_01HXX..." o "draft_01HXX...")
    display_id: str        # "#1247" — Medusa display_id formateado
    customer: str          # nombre completo o email si no hay nombre
    short: str             # 2 letras del avatar
    color: Literal["a", "b", "c", "d", "e", "f"]
    phone: str | None
    city: str | None
    channel: str           # "WhatsApp" | "Web" | etc. (puede ser default)
    status: OrderUiStatus
    pay_status: OrderPayStatus
    pay_type: OrderPayType
    items: int             # count de items distintos
    pieces: int            # sum de quantities
    total_cop: int         # major units, sin centavos
    currency_code: str
    is_draft: bool         # True si vino de /admin/draft-orders
    # Operacional / due date:
    due_iso: str | None    # YYYY-MM-DD — estimate hoy, real cuando llegue eta
    due_time: str | None   # HH:MM — "—" hoy
    overdue: bool          # derivado client-side (siempre False desde backend)
    priority: Literal["alta", "normal", "baja"]
    agent: str             # "—" si no hay
    created_at_ms: int     # ISO datetime de Medusa → ms epoch
    updated_at_ms: int


@dataclass(frozen=True)
class OrderDetailDTO:
    """Shape rica para el inspector — corresponde a `useOrderDetail(id)`.

    Incluye TODO el shape de `OrderSummaryDTO` (composicion) + items[] +
    addresses + timeline + payment + metadata para "Datos pendientes"
    marker.
    """
    summary: OrderSummaryDTO
    items_detail: list[OrderItemDTO]
    shipping_address: OrderAddressDTO | None
    billing_address: OrderAddressDTO | None
    subtotal_cop: int
    shipping_cop: int
    tax_total_cop: int
    discount_total_cop: int
    timeline: list[OrderTimelineEventDTO]
    payment_method_label: str | None      # "Transferencia" | "Contra entrega" | None
    notes: list[str] = field(default_factory=list)  # vacio hoy
    # Cuales slots NO tenemos en Medusa, para que la UI pinte marker.
    # Valores: 'due_date', 'agent', 'priority', 'notes', 'customer_history',
    # 'tracking_number', 'shipping_provider'.
    data_completeness_missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrderListDTO:
    """Envelope de la respuesta de `list_orders(...)`."""
    orders: list[OrderSummaryDTO]
    count: int
    offset: int
    limit: int
    catalog_available: bool   # False si Medusa rechazo / no responde
    error_detail: str | None = None


@runtime_checkable
class OrderQueryPort(Protocol):
    """Contrato para consultar ordenes para el dashboard.

    Implementaciones:
      * `MedusaOrderQuery` (live) — `medusa_order_query.py`.
      * `EmptyOrderQuery` (stub) — devuelve listas vacias + flag explicito
        de no-config (sirve cuando no hay Medusa en dev).
    """

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_drafts: bool = True,
    ) -> OrderListDTO: ...

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        """Devuelve detalle o None si no existe (404). Levanta para 5xx."""
        ...
