"""Tests del reconciler del Temporal Schedule de `orders/reconcile`.

Comportamiento exigido: `_ensure_schedule` debe CONVERGER el spec del Schedule
al valor de config (`ORDER_RECONCILE_INTERVAL_MINUTES`) AUNQUE el Schedule ya
exista. Antes hacía create-only y tragaba `ScheduleAlreadyRunningError` sin
actualizar → cambiar el env y reiniciar el worker NO movía el intervalo (el
Schedule es un objeto server-side de Temporal que persiste entre reinicios).

Ver HU schedulers-configurables (config vía ConfigMap de AWS sin redeploy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleState,
)

from src.plugins.orders.workers.reconcile import _SCHEDULE_ID, _ensure_schedule


def _old_schedule(minutes: int, *, paused: bool = False) -> Schedule:
    """Un Schedule 'ya existente' con un intervalo viejo (y opcional pausa)."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "OrderReconciliationWorkflow", args=[], id="x", task_queue="q"
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=minutes))]
        ),
        state=ScheduleState(paused=paused),
    )


@dataclass
class _StubDesc:
    schedule: Schedule


@dataclass
class _StubUpdateInput:
    description: _StubDesc


@dataclass
class _FakeHandle:
    current: Schedule
    updates: list = field(default_factory=list)

    async def update(self, updater) -> None:
        # El SDK invoca el updater con la descripción actual (puede repetirlo en
        # el loop de conflict-resolution). Replicamos esa forma con un stub
        # duck-typed: el updater solo accede a `inp.description.schedule`.
        result = updater(_StubUpdateInput(_StubDesc(self.current)))
        if result is not None:
            self.updates.append(result)


@dataclass
class _FakeClient:
    handle: _FakeHandle
    raise_exists: bool = True
    created: list = field(default_factory=list)

    async def create_schedule(self, schedule_id, schedule) -> None:
        if self.raise_exists:
            raise ScheduleAlreadyRunningError()
        self.created.append((schedule_id, schedule))

    def get_schedule_handle(self, schedule_id):
        assert schedule_id == _SCHEDULE_ID
        return self.handle


@pytest.mark.asyncio
async def test_ensure_schedule_converges_interval_when_already_exists(monkeypatch):
    monkeypatch.setenv("ORDER_RECONCILE_INTERVAL_MINUTES", "7")
    handle = _FakeHandle(current=_old_schedule(5))
    client = _FakeClient(handle=handle)

    await _ensure_schedule(client, "queue-orders-reconcile")

    assert handle.updates, (
        "esperaba que _ensure_schedule ACTUALICE el Schedule existente al "
        "intervalo del env; create_schedule no actualiza un schedule ya creado"
    )
    new_spec = handle.updates[-1].schedule.spec
    assert new_spec.intervals[0].every == timedelta(minutes=7)


@pytest.mark.asyncio
async def test_ensure_schedule_preserves_operator_pause_on_converge(monkeypatch):
    # Converger el spec NO debe reactivar un Schedule pausado a mano por el
    # operador desde la Temporal UI — solo se sincroniza el intervalo.
    monkeypatch.setenv("ORDER_RECONCILE_INTERVAL_MINUTES", "9")
    handle = _FakeHandle(current=_old_schedule(5, paused=True))
    client = _FakeClient(handle=handle)

    await _ensure_schedule(client, "queue-orders-reconcile")

    updated = handle.updates[-1].schedule
    assert updated.spec.intervals[0].every == timedelta(minutes=9)
    assert updated.state.paused is True
