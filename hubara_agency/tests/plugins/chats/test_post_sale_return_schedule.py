"""Tests del reconciler del Temporal Schedule del scheduler post-venta.

Mismo contrato que `sales-eval-schedule` (molde:
tests/plugins/chats/test_post_sale_return_schedule.py ← test_sales_eval_schedule.py):

* Schedule ausente → se crea con el cron de config (tz America/Bogota) y
  overlap SKIP.
* Schedule existente → CONVERGE el cron al valor de config (create-only no
  actualiza server-side; cambiar el env y reiniciar debe mover el horario).
* `POST_SALE_RETURN_SCHEDULE_ENABLED=false` → BORRA el schedule existente
  (toggle real, INV-2 — no solo skipear la creación).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    ScheduleSpec,
)

from src.plugins.chats.workers.post_sale_return import (
    _SCHEDULE_ID,
    _ensure_schedule,
)


def _cron_schedule(cron: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "PostSaleReturnWorkflow", args=[], id="x", task_queue="q"
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
    deleted: bool = False

    async def update(self, updater) -> None:
        result = updater(_StubUpdateInput(_StubDesc(self.current)))
        if result is not None:
            self.updates.append(result)

    async def delete(self) -> None:
        self.deleted = True


@dataclass
class _FakeClient:
    handle: _FakeHandle
    expected_id: str
    exists: bool = True
    created: list = field(default_factory=list)

    async def create_schedule(self, schedule_id, schedule) -> None:
        if self.exists:
            raise ScheduleAlreadyRunningError()
        self.created.append((schedule_id, schedule))

    def get_schedule_handle(self, schedule_id):
        assert schedule_id == self.expected_id
        return self.handle


@pytest.mark.asyncio
async def test_crea_el_schedule_con_cron_de_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POST_SALE_RETURN_SCHEDULE_CRON", "0 5 * * *")
    client = _FakeClient(
        handle=_FakeHandle(_cron_schedule("0 21 * * *")),
        expected_id=_SCHEDULE_ID,
        exists=False,
    )
    await _ensure_schedule(client, "queue-post-sale-return")

    assert len(client.created) == 1
    schedule_id, schedule = client.created[0]
    assert schedule_id == _SCHEDULE_ID
    assert schedule.spec.cron_expressions == ["0 5 * * *"]
    assert schedule.spec.time_zone_name == "America/Bogota"
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert client.handle.updates == []


@pytest.mark.asyncio
async def test_converge_el_cron_si_el_schedule_ya_existe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POST_SALE_RETURN_SCHEDULE_CRON", "0 4 * * *")
    client = _FakeClient(
        handle=_FakeHandle(_cron_schedule("0 21 * * *")),
        expected_id=_SCHEDULE_ID,
        exists=True,
    )
    await _ensure_schedule(client, "queue-post-sale-return")

    assert client.created == []
    assert len(client.handle.updates) == 1
    updated_spec = client.handle.updates[0].schedule.spec
    assert updated_spec.cron_expressions == ["0 4 * * *"]
    assert updated_spec.time_zone_name == "America/Bogota"


@pytest.mark.asyncio
async def test_toggle_off_borra_el_schedule_existente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POST_SALE_RETURN_SCHEDULE_ENABLED", "false")
    client = _FakeClient(
        handle=_FakeHandle(_cron_schedule("0 21 * * *")),
        expected_id=_SCHEDULE_ID,
        exists=True,
    )
    await _ensure_schedule(client, "queue-post-sale-return")

    assert client.created == []
    assert client.handle.updates == []
    assert client.handle.deleted is True
