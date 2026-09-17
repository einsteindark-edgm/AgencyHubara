"""Tests del OrderReconciliationWorkflow + reconcile_pending_orders_activity.

- El workflow se prueba con `WorkflowEnvironment.start_time_skipping()` + una
  activity fake (mismo patrón que tests/catalog_sync/test_workflow.py): valida
  que el workflow ejecuta la activity y devuelve el ReconcileResult.
- La activity REAL se prueba con `ActivityEnvironment` + un FakePort + vault
  tmp: valida que envuelve `reconcile_all_pending` y persiste el estado.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from src.platform.orders.port import OrderRegistrationResult
from src.plugins.orders.agent.activities.reconcile import (
    reconcile_pending_orders_activity,
)
from src.plugins.orders.agent.contracts import ReconcileInput, ReconcileResult
from src.plugins.orders.agent.workflows import OrderReconciliationWorkflow


# ----------------------------------------------------------------------
# Workflow — WorkflowEnvironment + fake activity
# ----------------------------------------------------------------------


@activity.defn(name="reconcile_pending_orders")
async def fake_reconcile(input: ReconcileInput) -> ReconcileResult:
    # El workflow debe pasar el input tal cual.
    assert input.max_attempts == 3
    return ReconcileResult(
        total=3, resolved=2, still_failing=1, abandoned=0, errors=0,
    )


@pytest.mark.asyncio
async def test_workflow_runs_activity_and_returns_result():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-orders-reconcile",
            workflows=[OrderReconciliationWorkflow],
            activities=[fake_reconcile],
        ):
            result = await env.client.execute_workflow(
                OrderReconciliationWorkflow.run,
                ReconcileInput(vault_dir="/tmp/x", max_attempts=3),
                id="test-reconcile-1",
                task_queue="test-orders-reconcile",
            )
            assert result.total == 3
            assert result.resolved == 2
            assert result.still_failing == 1
            assert result.abandoned == 0
            assert result.errors == 0


# ----------------------------------------------------------------------
# Activity real — ActivityEnvironment + FakePort + vault tmp
# ----------------------------------------------------------------------


@dataclass
class FakePort:
    succeed: bool
    calls: list = field(default_factory=list)

    async def register_order(self, **kwargs) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        if self.succeed:
            return OrderRegistrationResult(
                success=True, order_id="draft_OK", provider="medusa",
            )
        return OrderRegistrationResult(
            success=False, order_id=None, provider="medusa",
            error_detail="still down",
        )


def _write_failed(vault: Path, session: str, order_id: str) -> None:
    d = vault / session
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "order_id": order_id, "provider": "medusa", "success": False,
        "items": [{"handle": "vela", "quantity": 1, "unit_price_cop": 10000}],
        "shipping": {"city": "Bogotá", "neighborhood": "Centro",
                     "address": "C1", "phone": "+57"},
        "payment_method": "transfer", "subtotal_cop": 10000, "shipping_cop": 0,
        "total_cop": 10000, "currency": "COP",
        "registered_at_ms": 1779800000000, "status": "pending",
    }
    (d / "metadata.json").write_text(
        json.dumps({"failed_order_registrations": [rec]}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_activity_resolves_pending(tmp_path, monkeypatch):
    _write_failed(tmp_path, "wa_1", "AUDIT-1")
    import src.plugins.orders.agent.activities.reconcile as act
    port = FakePort(succeed=True)
    monkeypatch.setattr(act, "get_order_registration_port", lambda: port)

    env = ActivityEnvironment()
    result = await env.run(
        reconcile_pending_orders_activity,
        ReconcileInput(vault_dir=str(tmp_path)),
    )

    assert result.total == 1
    assert result.resolved == 1
    assert result.still_failing == 0
    assert len(port.calls) == 1
    # Estado persistido.
    data = json.loads((tmp_path / "wa_1" / "metadata.json").read_text())
    assert data["failed_order_registrations"][0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_activity_reports_still_failing(tmp_path, monkeypatch):
    _write_failed(tmp_path, "wa_1", "AUDIT-1")
    import src.plugins.orders.agent.activities.reconcile as act
    port = FakePort(succeed=False)
    monkeypatch.setattr(act, "get_order_registration_port", lambda: port)

    env = ActivityEnvironment()
    result = await env.run(
        reconcile_pending_orders_activity,
        ReconcileInput(vault_dir=str(tmp_path)),
    )

    assert result.total == 1
    assert result.resolved == 0
    assert result.still_failing == 1
    data = json.loads((tmp_path / "wa_1" / "metadata.json").read_text())
    assert data["failed_order_registrations"][0]["status"] == "pending"
