"""Validar el cupón de una campaña en el webhook sin escanear Medusa una vez
por cliente.

Cada primera respuesta a una campaña con cupón lo valida en el webhook
(L-32). En una campaña masiva eso son decenas de respuestas por minuto: sin
un memo, cada una leería los pedidos de Medusa para contar las vendidas del
cupo. Las vendidas solo deciden qué se OFRECE; el registro las relee bajo el
candado, así que unos segundos de atraso no venden de más.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.platform.promotions.port import PromotionsUnavailableError
from src.plugins.chats.agent.sales.use_cases.coupon_application import RecentSoldUnits

SINCE = datetime(2026, 9, 24, 19, 16, tzinfo=timezone.utc)


class _Reader:
    def __init__(self, sold: dict[str, int] | None = None, *, down: bool = False) -> None:
        self.sold = sold or {}
        self.down = down
        self.calls = 0

    async def sold_units(self, *, since: datetime, exclude=None) -> dict[str, int]:
        self.calls += 1
        await asyncio.sleep(0)
        if self.down:
            raise PromotionsUnavailableError("timeout")
        return dict(self.sold)


@pytest.mark.asyncio
async def test_campaign_replies_share_one_sales_read_for_a_while() -> None:
    reader = _Reader({"q1": 1})
    clock = [0.0]
    memo = RecentSoldUnits(reader, ttl_s=30, clock=lambda: clock[0])

    results = await asyncio.gather(*(memo.sold_units(since=SINCE) for _ in range(10)))

    assert reader.calls == 1
    assert all(r == {"q1": 1} for r in results)
    clock[0] = 31.0
    await memo.sold_units(since=SINCE)
    assert reader.calls == 2


@pytest.mark.asyncio
async def test_a_failed_read_is_not_remembered() -> None:
    reader = _Reader(down=True)
    memo = RecentSoldUnits(reader, ttl_s=30, clock=lambda: 0.0)

    for _ in range(2):
        with pytest.raises(PromotionsUnavailableError):
            await memo.sold_units(since=SINCE)

    assert reader.calls == 2


@pytest.mark.asyncio
async def test_reads_that_exclude_an_order_go_straight_to_medusa() -> None:
    reader = _Reader({"q1": 1})
    memo = RecentSoldUnits(reader, ttl_s=30, clock=lambda: 0.0)

    await memo.sold_units(since=SINCE, exclude={("wa_x", "fp")})
    await memo.sold_units(since=SINCE, exclude={("wa_x", "fp")})

    assert reader.calls == 2
