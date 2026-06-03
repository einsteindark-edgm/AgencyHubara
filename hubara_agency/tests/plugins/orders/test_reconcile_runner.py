"""Tests del batch driver de reconciliación (`plugins/orders/reconcile_runner.py`).

Verifica que el barrido:
  * solo reintenta los `pending` (no resueltos, no abandonados),
  * agrega el summary correctamente,
  * es idempotente (no reprocesa lo ya terminal).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.platform.orders.port import (
    OrderRegistrationResult,
)
from src.platform.orders.reconciliation import (
    STATUS_ABANDONED,
    STATUS_PENDING,
    STATUS_RESOLVED,
)
from src.plugins.orders.reconcile_runner import reconcile_all_pending


@dataclass
class FakePort:
    """Devuelve éxito o fallo según el handle del primer item (para rutear)."""

    succeed_handles: set[str] = field(default_factory=set)
    fail_all: bool = False
    calls: list[str] = field(default_factory=list)

    async def register_order(
        self, *, session_key, items, shipping, payment_method,
        subtotal_cop, shipping_cop, total_cop, currency="COP",
    ) -> OrderRegistrationResult:
        handle = items[0].handle if items else "?"
        self.calls.append(handle)
        if self.fail_all or handle not in self.succeed_handles:
            return OrderRegistrationResult(
                success=False, order_id=None, provider="medusa",
                error_detail="still down",
            )
        return OrderRegistrationResult(
            success=True, order_id=f"draft_{handle}", provider="medusa",
        )


def _failed_record(order_id, handle, status=STATUS_PENDING, **extra) -> dict:
    rec = {
        "order_id": order_id,
        "provider": "medusa",
        "success": False,
        "items": [{"handle": handle, "quantity": 1, "unit_price_cop": 10000}],
        "shipping": {"city": "Bogotá", "neighborhood": "Centro",
                     "address": "Calle 1", "phone": "+57300"},
        "payment_method": "transfer",
        "subtotal_cop": 10000, "shipping_cop": 0, "total_cop": 10000,
        "currency": "COP", "registered_at_ms": 1779800000000,
        "status": status,
    }
    rec.update(extra)
    return rec


def _write(vault: Path, session_key: str, data: dict) -> None:
    d = vault / session_key
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read(vault: Path, session_key: str) -> dict:
    return json.loads((vault / session_key / "metadata.json").read_text())


@pytest.mark.asyncio
async def test_empty_vault_summary_all_zeros(tmp_path):
    port = FakePort()
    summary = await reconcile_all_pending(vault_dir=tmp_path, port=port)
    assert summary.total == 0
    assert summary.resolved == 0
    assert len(port.calls) == 0


@pytest.mark.asyncio
async def test_resolves_pending(tmp_path):
    _write(tmp_path, "wa_1", {
        "failed_order_registrations": [_failed_record("AUDIT-1", "vela-ok")],
    })
    port = FakePort(succeed_handles={"vela-ok"})

    summary = await reconcile_all_pending(vault_dir=tmp_path, port=port)

    assert summary.total == 1
    assert summary.resolved == 1
    assert summary.still_failing == 0
    rec = _read(tmp_path, "wa_1")["failed_order_registrations"][0]
    assert rec["status"] == STATUS_RESOLVED
    assert rec["resolved_order_id"] == "draft_vela-ok"


@pytest.mark.asyncio
async def test_skips_resolved_and_abandoned(tmp_path):
    """resolved no está en el scan; abandoned está pero NO se reintenta."""
    _write(tmp_path, "wa_1", {
        "failed_order_registrations": [
            _failed_record("AUDIT-pending", "vela-ok"),
            _failed_record("AUDIT-resolved", "vela-x", status=STATUS_RESOLVED),
            _failed_record("AUDIT-abandoned", "vela-y", status=STATUS_ABANDONED),
        ],
    })
    port = FakePort(succeed_handles={"vela-ok"})

    summary = await reconcile_all_pending(vault_dir=tmp_path, port=port)

    # Solo el pending se procesó.
    assert summary.total == 1
    assert summary.resolved == 1
    assert port.calls == ["vela-ok"]  # NO tocó resolved ni abandoned


@pytest.mark.asyncio
async def test_still_failing_across_sessions(tmp_path):
    _write(tmp_path, "wa_1", {
        "failed_order_registrations": [_failed_record("AUDIT-1", "h1")],
    })
    _write(tmp_path, "wa_2", {
        "failed_order_registrations": [_failed_record("AUDIT-2", "h2")],
    })
    port = FakePort(fail_all=True)

    summary = await reconcile_all_pending(vault_dir=tmp_path, port=port)

    assert summary.total == 2
    assert summary.still_failing == 2
    assert summary.resolved == 0
    assert sorted(port.calls) == ["h1", "h2"]


@pytest.mark.asyncio
async def test_partial_resolution(tmp_path):
    _write(tmp_path, "wa_1", {
        "failed_order_registrations": [
            _failed_record("AUDIT-1", "good"),
            _failed_record("AUDIT-2", "bad"),
        ],
    })
    port = FakePort(succeed_handles={"good"})

    summary = await reconcile_all_pending(vault_dir=tmp_path, port=port)

    assert summary.total == 2
    assert summary.resolved == 1
    assert summary.still_failing == 1


@pytest.mark.asyncio
async def test_runner_is_idempotent_on_second_pass(tmp_path):
    """Segunda corrida: el ya-resuelto no reaparece, no se vuelve a llamar."""
    _write(tmp_path, "wa_1", {
        "failed_order_registrations": [_failed_record("AUDIT-1", "good")],
    })
    port = FakePort(succeed_handles={"good"})

    first = await reconcile_all_pending(vault_dir=tmp_path, port=port)
    second = await reconcile_all_pending(vault_dir=tmp_path, port=port)

    assert first.resolved == 1
    assert second.total == 0  # ya no hay pendientes
    assert port.calls == ["good"]  # solo una vez
