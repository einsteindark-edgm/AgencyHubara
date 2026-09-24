"""Candado por código + guarda "la última unidad" (Fase 4).

Dos clientes confirman la última unidad: al registrar, bajo el candado del
código, se relee lo vendido y se recalcula el reparto; si ya no coincide con
lo confirmado, NO se registra (`quota_changed`, con el reparto nuevo).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.platform.promotions.quota_lock import (
    QuotaChanged,
    QuotaLockTimeout,
    VaultQuotaLock,
    register_under_quota,
)


@pytest.mark.asyncio
async def test_register_with_quota_never_sells_the_last_unit_twice(tmp_path: Path) -> None:
    lock = VaultQuotaLock(tmp_path)
    sold: dict[str, int] = {"q1": 4}  # de 5: queda 1
    registered: list[str] = []

    async def read_sold() -> dict[str, int]:
        await asyncio.sleep(0.01)
        return dict(sold)

    def allocation(current: dict[str, int]) -> int:
        return max(5 - current.get("q1", 0), 0) and 1  # 1 unidad con descuento si queda

    async def register(who: str) -> str:
        await asyncio.sleep(0.02)  # Medusa tarda: la carrera está abierta sin candado
        sold["q1"] = sold.get("q1", 0) + 1
        registered.append(who)
        return f"order_{who}"

    async def attempt(who: str):
        return await register_under_quota(
            lock, "AMOR26",
            read_sold=read_sold,
            allocate=allocation,
            confirmed=1,
            register=lambda: register(who),
        )

    first, second = await asyncio.gather(attempt("ana"), attempt("luis"))

    results = sorted([first, second], key=lambda r: isinstance(r, QuotaChanged))
    assert results[0] in ("order_ana", "order_luis")
    assert results[1] == QuotaChanged(allocation=0)
    assert len(registered) == 1


@pytest.mark.asyncio
async def test_lock_is_per_code(tmp_path: Path) -> None:
    lock = VaultQuotaLock(tmp_path)
    async with lock.hold("AMOR26", timeout_s=1):
        async with lock.hold("OTRO", timeout_s=1):
            pass  # otro código no espera


@pytest.mark.asyncio
async def test_lock_times_out_instead_of_hanging(tmp_path: Path) -> None:
    lock = VaultQuotaLock(tmp_path)
    async with lock.hold("AMOR26", timeout_s=1):
        with pytest.raises(QuotaLockTimeout):
            async with lock.hold("amor26", timeout_s=0.2):  # mismo código
                pass
