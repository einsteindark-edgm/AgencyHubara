"""El Temporal Schedule de la sonda diaria de Jev (worker `sales_eval`).

Mismo contrato que `sales-eval-schedule` (molde: test_sales_eval_schedule.py y
test_post_sale_return_schedule.py):

* Schedule ausente → se crea a las 07:00 de Bogotá con overlap SKIP y arranca
  `DecisionsProbeWorkflow` con id fijo.
* Schedule existente → CONVERGE el cron al valor de config
  (`DECISIONS_PROBE_SCHEDULE_CRON`); reiniciar el worker no lo duplica.
* `DECISIONS_PROBE_SCHEDULE_ENABLED=false` → BORRA el schedule existente
  (toggle real, INV-2).

Y el worker registra el workflow y la activity de la sonda.
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
    ScheduleOverlapPolicy,
    ScheduleSpec,
)

from src.plugins.chats.workers import sales_eval as worker  # noqa: E402

QUEUE = "queue-sales-eval"


def _cron_schedule(cron: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow("DecisionsProbeWorkflow", args=[], id="x", task_queue="q"),
        spec=ScheduleSpec(cron_expressions=[cron], time_zone_name="America/Bogota"),
    )


@dataclass
class _StubDesc:
    schedule: Schedule


@dataclass
class _StubUpdateInput:
    description: _StubDesc


@dataclass
class _Handle:
    client: "_Temporal"
    schedule_id: str
    updates: list = field(default_factory=list)

    async def update(self, updater) -> None:
        result = updater(_StubUpdateInput(_StubDesc(self.client.schedules[self.schedule_id])))
        if result is not None:
            self.updates.append(result)
            self.client.schedules[self.schedule_id] = result.schedule

    async def delete(self) -> None:
        if self.schedule_id not in self.client.schedules:
            raise RuntimeError("no existe")
        del self.client.schedules[self.schedule_id]


@dataclass
class _Temporal:
    """Temporal falso con estado: guarda los schedules por id."""

    schedules: dict = field(default_factory=dict)
    created: list = field(default_factory=list)
    handles: dict = field(default_factory=dict)

    async def create_schedule(self, schedule_id, schedule) -> None:
        if schedule_id in self.schedules:
            raise ScheduleAlreadyRunningError()
        self.schedules[schedule_id] = schedule
        self.created.append(schedule_id)

    def get_schedule_handle(self, schedule_id):
        return self.handles.setdefault(schedule_id, _Handle(self, schedule_id))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DECISIONS_PROBE_SCHEDULE_ENABLED", raising=False)
    monkeypatch.delenv("DECISIONS_PROBE_SCHEDULE_CRON", raising=False)


async def test_creates_the_daily_schedule_at_seven_bogota() -> None:
    client = _Temporal()

    await worker._ensure_probe_schedule(client, QUEUE)

    assert client.created == [worker._PROBE_SCHEDULE_ID] == ["decisions-probe-schedule"]
    schedule = client.schedules[worker._PROBE_SCHEDULE_ID]
    assert schedule.spec.cron_expressions == ["0 7 * * *"]
    assert schedule.spec.time_zone_name == "America/Bogota"
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    action = schedule.action
    assert (action.workflow, action.id, action.task_queue) == ("DecisionsProbeWorkflow", "decisions-probe", QUEUE)
    assert list(action.args) == []


async def test_converges_the_cron_when_the_schedule_already_exists(monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_PROBE_SCHEDULE_CRON", "30 6 * * *")
    client = _Temporal(schedules={worker._PROBE_SCHEDULE_ID: _cron_schedule("0 7 * * *")})

    await worker._ensure_probe_schedule(client, QUEUE)

    assert client.created == []
    assert worker._PROBE_SCHEDULE_ID in client.handles, "esperaba que converja el schedule existente"
    handle = client.handles[worker._PROBE_SCHEDULE_ID]
    assert len(handle.updates) == 1
    spec = handle.updates[0].schedule.spec
    assert spec.cron_expressions == ["30 6 * * *"] and spec.time_zone_name == "America/Bogota"


async def test_rebooting_the_worker_does_not_duplicate_the_schedule() -> None:
    client = _Temporal()

    await worker._ensure_probe_schedule(client, QUEUE)
    await worker._ensure_probe_schedule(client, QUEUE)

    assert client.created == [worker._PROBE_SCHEDULE_ID]
    assert list(client.schedules) == [worker._PROBE_SCHEDULE_ID]
    assert len(client.handles[worker._PROBE_SCHEDULE_ID].updates) == 1


async def test_turning_the_toggle_off_deletes_the_schedule(monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_PROBE_SCHEDULE_ENABLED", "false")
    client = _Temporal(schedules={worker._PROBE_SCHEDULE_ID: _cron_schedule("0 7 * * *")})

    await worker._ensure_probe_schedule(client, QUEUE)

    assert client.created == [] and worker._PROBE_SCHEDULE_ID not in client.schedules


async def test_turning_the_toggle_off_without_a_schedule_is_fine(monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_PROBE_SCHEDULE_ENABLED", "false")
    client = _Temporal()

    await worker._ensure_probe_schedule(client, QUEUE)

    assert client.created == [] and client.schedules == {}


async def test_the_worker_registers_the_probe_and_ensures_its_schedule(monkeypatch) -> None:
    from src.plugins.chats.agent.sales_eval.activities.decisions_probe import run_decisions_probe_activity
    from src.plugins.chats.agent.sales_eval.workflows.decisions_probe import DecisionsProbeWorkflow

    client = _Temporal()
    seen: dict = {}

    class _Worker:
        def __init__(self, _client, *, task_queue, workflows, activities, **_kw) -> None:
            seen.update(task_queue=task_queue, workflows=list(workflows), activities=list(activities))

        async def run(self) -> None:
            return None

    async def _client() -> _Temporal:
        return client

    monkeypatch.setattr(worker, "ensure_plugin_enabled", lambda _plugin: None)
    monkeypatch.setattr(worker, "get_temporal_client", _client)
    monkeypatch.setattr(worker, "get_task_queue", lambda _plugin, _worker: QUEUE)
    monkeypatch.setattr(worker, "Worker", _Worker)

    await worker.main()

    assert DecisionsProbeWorkflow in seen["workflows"]
    assert run_decisions_probe_activity in seen["activities"]
    assert worker._PROBE_SCHEDULE_ID in client.schedules
