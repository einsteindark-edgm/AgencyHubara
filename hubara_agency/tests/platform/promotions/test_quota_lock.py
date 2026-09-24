"""Candado por código de cupón (Fase 4).

La guarda de "la última unidad" completa (dos registros concurrentes → uno
`quota_changed`) vive en
`tests/plugins/chats/sales/test_coupon_quota_orders.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.promotions.quota_lock import QuotaLockTimeout, VaultQuotaLock


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


@pytest.mark.asyncio
async def test_lock_accepts_any_existing_code_shape(tmp_path: Path) -> None:
    # Cupones viejos de Medusa pueden tener `_` o `-`: igual se pueden bloquear.
    lock = VaultQuotaLock(tmp_path)
    async with lock.hold("velas_10", timeout_s=1):
        with pytest.raises(QuotaLockTimeout):
            async with lock.hold("VELAS_10", timeout_s=0.1):
                pass
