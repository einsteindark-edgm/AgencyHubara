"""StubOrderRegistration — fallback in-process del OrderRegistrationPort.

Uso:
  * Dev local sin Medusa configurado (no hay `MEDUSA_REGION_ID`).
  * Smoke tests donde no querramos golpear la API real.

NO escribe a metadata.json (eso lo hace la tool `RegisterOrderTool` siempre,
sin importar el provider). Solo loguea + devuelve un order_id sintetico.

`provider="stub"` permite distinguir auditoramente entre pedidos que fueron
a Medusa de verdad (`provider="medusa"`) vs los que cayeron al stub. El
dashboard puede mostrar un badge para los stub orders.
"""
from __future__ import annotations

import logging
import time
import uuid

from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationResult,
    OrderShipping,
)

log = logging.getLogger(__name__)


class StubOrderRegistration:
    """Fallback que solo genera un order_id local."""

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> OrderRegistrationResult:
        order_id = f"HUB-{session_key}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        log.warning(
            "📦 StubOrderRegistration: NO medusa config — generating local "
            "order_id=%s for session=%s. Configure MEDUSA_REGION_ID + "
            "MEDUSA_SALES_CHANNEL_ID to register real Medusa orders.",
            order_id, session_key,
        )
        return OrderRegistrationResult(
            success=True,
            order_id=order_id,
            provider="stub",
            raw_payload=None,
        )
