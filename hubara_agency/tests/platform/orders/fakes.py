"""Dobles del `OrderQueryPort` para probar el `OrderFactsStore` de verdad: la
contract suite de OrderFacts y los lectores que dependen de su caché (p. ej. el
exportador del banco del laboratorio)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.platform.orders.query_port import OrderDetailDTO, OrderListDTO, OrderSummaryDTO


def order_summary(order_id: str, total: int, *, pay: str = "paid", stage: str = "preparing") -> OrderSummaryDTO:
    return OrderSummaryDTO(
        id=order_id,
        display_id=f"#{order_id[-2:]}",
        customer="Ana P",
        short="AP",
        color="a",
        phone=None,
        city=None,
        channel="WhatsApp",
        status=stage,  # type: ignore[arg-type]
        pay_status=pay,  # type: ignore[arg-type]
        pay_type="confirmed",
        items=1,
        pieces=1,
        total_cop=total,
        currency_code="cop",
        is_draft=False,
        due_iso=None,
        due_time=None,
        overdue=False,
        priority="normal",
        agent="—",
        created_at_ms=1,
        updated_at_ms=1,
    )


@dataclass
class FakeQuery:
    """OrderQueryPort de mentira: una lista paginada que se puede editar."""

    orders: list[OrderSummaryDTO]
    available: bool = True
    list_calls: list[int] = field(default_factory=list)

    async def list(self, *, limit: int = 50, offset: int = 0, include_drafts: bool = True) -> OrderListDTO:
        self.list_calls.append(offset)
        if not self.available:
            return OrderListDTO(orders=[], count=0, offset=offset, limit=limit, catalog_available=False, error_detail="down")
        page = self.orders[offset : offset + limit]
        return OrderListDTO(orders=page, count=len(self.orders), offset=offset, limit=limit, catalog_available=True)

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        return None

    def edit(self, order_id: str, **changes) -> None:
        self.orders = [replace(o, **changes) if o.id == order_id else o for o in self.orders]
