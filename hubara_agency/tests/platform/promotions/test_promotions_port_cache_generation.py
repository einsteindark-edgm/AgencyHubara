"""Cache del `MedusaPromotionsPort` vs `invalidate()` (premortem A11).

La central limpia el cache del proceso después de cada escritura. Una
lectura que YA estaba en vuelo (arrancó antes de la escritura) volvía y
guardaba en cache lo viejo: el proceso veía el cupón anterior hasta 60 s más.
Solo se guarda lo leído en la "generación" vigente del cache.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.platform.promotions.medusa import MedusaPromotionsPort


def _promo(value: int) -> dict[str, Any]:
    return {
        "id": "promo_1",
        "code": "AMOR27",
        "status": "active",
        "application_method": {"type": "percentage", "value": value, "target_type": "items"},
    }


class _SlowMedusa:
    """La primera lectura queda en vuelo hasta que el test la suelta."""

    def __init__(self) -> None:
        self.promotions = [_promo(10)]
        self.release = asyncio.Event()
        self.reads = 0

    async def list_promotions(self) -> list[dict[str, Any]]:
        snapshot = list(self.promotions)  # lo que había cuando llegó el pedido
        self.reads += 1
        if self.reads == 1:
            await self.release.wait()
        return snapshot

    async def list_product_tags(self, ids: list[str]) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_a_read_in_flight_during_invalidate_does_not_restore_stale_data() -> None:
    medusa = _SlowMedusa()
    port = MedusaPromotionsPort(medusa, ttl_s=3600)

    in_flight = asyncio.create_task(port.get_by_code("AMOR27"))
    await asyncio.sleep(0)  # la lectura vieja ya salió hacia Medusa
    medusa.promotions = [_promo(15)]  # la central edita el cupón…
    port.invalidate()  # …y limpia el cache del proceso
    medusa.release.set()
    old = await in_flight

    fresh = await port.get_by_code("AMOR27")

    assert old is not None and old.value == 10  # quien preguntó antes ve lo de antes
    assert fresh is not None and fresh.value == 15
    assert medusa.reads == 2


@pytest.mark.asyncio
async def test_without_invalidate_the_cache_still_serves_repeated_reads() -> None:
    medusa = _SlowMedusa()
    medusa.release.set()
    port = MedusaPromotionsPort(medusa, ttl_s=3600)

    await port.get_by_code("AMOR27")
    await port.list_active()

    assert medusa.reads == 1
