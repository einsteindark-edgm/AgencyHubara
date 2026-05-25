"""EmptyOrderQuery — fallback in-process del OrderQueryPort.

Uso:
  * Dev sin Medusa configurado (sin `MEDUSA_BASE_URL` o sin credenciales).
  * Tests donde no querramos golpear la API real.

Devuelve listas vacias con `catalog_available=False` y `error_detail`
explicito para que el frontend pinte un estado "Conecta Medusa para ver
ordenes" en lugar de un error 500.
"""
from __future__ import annotations

import logging

from src.platform.orders.query_port import OrderDetailDTO, OrderListDTO

log = logging.getLogger(__name__)


class EmptyOrderQuery:
    """Stub que indica explicitamente que no hay Medusa configurado."""

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_drafts: bool = True,
    ) -> OrderListDTO:
        log.debug("EmptyOrderQuery.list called (no medusa config)")
        return OrderListDTO(
            orders=[],
            count=0,
            offset=offset,
            limit=limit,
            catalog_available=False,
            error_detail=(
                "Medusa no esta configurado (falta MEDUSA_BASE_URL / "
                "MEDUSA_ADMIN_TOKEN). Las ordenes no se pueden consultar."
            ),
        )

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        log.debug("EmptyOrderQuery.get(%r) called (no medusa config)", order_id)
        return None
