"""Tests del reconciler de los Temporal Schedules de `chats/sales_eval`.

Tanto el eval ONLINE (`sales-eval-schedule`) como el GOLDEN
(`golden-eval-schedule`) deben CONVERGER su cron al valor de config
(`SALES_EVAL_SCHEDULE_CRON` / `GOLDEN_EVAL_SCHEDULE_CRON`) aunque el Schedule
ya exista. Antes hacían create-only → cambiar el cron y reiniciar el worker NO
movía el horario (el Schedule persiste server-side en Temporal).

Ver HU schedulers-configurables (config vía ConfigMap de AWS sin redeploy).
"""
from __future__ import annotations

import os

# init_otel() corre al importar el worker; sin esto intentaría montar
# exporters OTel reales en el test. Debe setearse ANTES del import del módulo.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from dataclasses import dataclass, field  # noqa: E402

import pytest  # noqa: E402
from temporalio.client import (  # noqa: E402
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
)

from src.plugins.chats.workers.sales_eval import (  # noqa: E402
    _GOLDEN_SCHEDULE_ID,
    _SCHEDULE_ID,
    _ensure_golden_schedule,
    _ensure_schedule,
)


def _cron_schedule(cron: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "SalesEvalWorkflow", args=[], id="x", task_queue="q"
        ),
        spec=ScheduleSpec(cron_expressions=[cron], time_zone_name="America/Bogota"),
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
        result = updater(_StubUpdateInput(_StubDesc(self.current)))
        if result is not None:
            self.updates.append(result)


@dataclass
class _FakeClient:
    handle: _FakeHandle
    expected_id: str
    created: list = field(default_factory=list)

    async def create_schedule(self, schedule_id, schedule) -> None:
        raise ScheduleAlreadyRunningError()

    def get_schedule_handle(self, schedule_id):
        assert schedule_id == self.expected_id
        return self.handle


@pytest.mark.asyncio
async def test_online_schedule_converges_cron_when_already_exists(monkeypatch):
    monkeypatch.setenv("SALES_EVAL_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("SALES_EVAL_SCHEDULE_CRON", "0 7 * * *")
    handle = _FakeHandle(current=_cron_schedule("0 23 * * *"))
    client = _FakeClient(handle=handle, expected_id=_SCHEDULE_ID)

    await _ensure_schedule(client, "queue-sales-eval")

    assert handle.updates, "esperaba que converja el cron del schedule existente"
    assert handle.updates[-1].schedule.spec.cron_expressions == ["0 7 * * *"]


@pytest.mark.asyncio
async def test_golden_schedule_converges_cron_when_already_exists(monkeypatch):
    monkeypatch.setenv("GOLDEN_EVAL_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("GOLDEN_EVAL_SCHEDULE_CRON", "30 5 * * *")
    handle = _FakeHandle(current=_cron_schedule("0 6 * * *"))
    client = _FakeClient(handle=handle, expected_id=_GOLDEN_SCHEDULE_ID)

    await _ensure_golden_schedule(client, "queue-sales-eval")

    assert handle.updates, "esperaba que converja el cron del golden schedule"
    assert handle.updates[-1].schedule.spec.cron_expressions == ["30 5 * * *"]
