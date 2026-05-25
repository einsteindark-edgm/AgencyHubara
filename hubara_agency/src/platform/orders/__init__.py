"""platform.orders — port + adapters para registrar pedidos en la fuente
de la verdad (Medusa Orders v2 vía Draft Orders).

R-DIP: este paquete NO importa de ningun agente. Sus consumers viven en
`src/plugins/chats/agent/sales/tools/order_registration.py` (RegisterOrderTool)
y reciben el `OrderRegistrationPort` via constructor injection (composition
root: `src/plugins/chats/workers/sales.py`).

Patron: mismo shape que `src/platform/catalog/checkout_port.py` +
`src/platform/catalog/medusa_checkout.py` para verify_order_for_checkout —
Protocol abstracto + adapter live + DTOs `@dataclass(frozen=True)` JSON-safe.
"""
from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationPort,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.query_port import (
    OrderAddressDTO,
    OrderDetailDTO,
    OrderItemDTO,
    OrderListDTO,
    OrderQueryPort,
    OrderSummaryDTO,
    OrderTimelineEventDTO,
)

__all__ = [
    # write port
    "OrderItem",
    "OrderRegistrationPort",
    "OrderRegistrationResult",
    "OrderShipping",
    # read port
    "OrderAddressDTO",
    "OrderDetailDTO",
    "OrderItemDTO",
    "OrderListDTO",
    "OrderQueryPort",
    "OrderSummaryDTO",
    "OrderTimelineEventDTO",
]
