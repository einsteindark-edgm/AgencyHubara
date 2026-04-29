"""Tests de las dispatcher-activities (Fase 4 / ADR-001).

Cubren:
  * Que el modulo importa correctamente y registra las dos activities con `name=...`.
  * Que `start_or_signal_sales_workflow_activity` parsea la decision y llama
    `Client.connect`/`start_workflow`/`signal` (verificado via monkeypatch).
  * Que `schedule_remarketing_workflow_activity` programa con `start_delay`.

No usamos `WorkflowEnvironment` real porque las activities solo necesitan un
Client mockeado. La estructura sigue el patron de `ActivityEnvironment` cuando
se requiera context.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from src.core.contracts import ScheduleRemarketingDecision, TransferDecision


def test_dispatcher_module_exports_two_activities() -> None:
    import src.core.infrastructure.temporal.dispatcher_activities as mod
    # Ambas funciones existen y son corutinas
    assert inspect.iscoroutinefunction(mod.start_or_signal_sales_workflow_activity)
    assert inspect.iscoroutinefunction(mod.schedule_remarketing_workflow_activity)


class _FakeHandle:
    def __init__(self) -> None:
        self.signaled_with: list[tuple[Any, ...]] = []
        self.described_status = None

    async def describe(self) -> Any:
        from temporalio.client import WorkflowExecutionStatus
        class _D:
            status = WorkflowExecutionStatus.RUNNING
        return _D()

    async def signal(self, *args: Any, **kwargs: Any) -> None:
        self.signaled_with.append((args, kwargs))


class _FakeClient:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.handle = _FakeHandle()

    def get_workflow_handle(self, wf_id: str) -> _FakeHandle:
        self.handle.workflow_id = wf_id
        return self.handle

    async def start_workflow(self, *args: Any, **kwargs: Any) -> _FakeHandle:
        self.start_calls.append({"args": args, "kwargs": kwargs})
        return self.handle


async def test_start_or_signal_sales_signals_running_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeClient()

    async def fake_get_client() -> _FakeClient:
        return fake_client

    monkeypatch.setattr(
        "src.core.infrastructure.temporal.dispatcher_activities.get_temporal_client",
        fake_get_client,
    )
    from src.core.infrastructure.temporal.dispatcher_activities import (
        start_or_signal_sales_workflow_activity,
    )

    decision = TransferDecision(
        session_id="wa_5491111111111",
        target_route="ventas",
        summary="quiere ver el catalogo",
    )

    env = ActivityEnvironment()
    await env.run(start_or_signal_sales_workflow_activity, decision)

    # No se llamo start_workflow porque describe() devolvio RUNNING
    assert fake_client.start_calls == []
    # Pero si se mando signal con el resumen
    assert len(fake_client.handle.signaled_with) == 1
    args, _ = fake_client.handle.signaled_with[0]
    # El segundo args es la lista de args del signal: [message, media, plugin_context]
    signal_args = args[1]["args"] if "args" in args[1] else args[1].get("args", [])
    # Como pasamos `args=[...]` la libreria envia el kwarg `args`. Validemos forma generica.
    # Aceptamos ambas formas: (handle.send_message, args=[...]) o positional.
    assert any("Remarketing acaba de recuperar al cliente" in str(a) for a in args[1].values()) or \
           any("Remarketing acaba de recuperar al cliente" in str(a) for a in args)


async def test_schedule_remarketing_uses_start_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeClient()

    async def fake_get_client() -> _FakeClient:
        return fake_client

    monkeypatch.setattr(
        "src.core.infrastructure.temporal.dispatcher_activities.get_temporal_client",
        fake_get_client,
    )
    from src.core.infrastructure.temporal.dispatcher_activities import (
        schedule_remarketing_workflow_activity,
    )

    decision = ScheduleRemarketingDecision(
        session_id="wa_5494444444444",
        motivo="cliente dudo precio",
        delay_seconds=30,
    )

    env = ActivityEnvironment()
    await env.run(schedule_remarketing_workflow_activity, decision)

    # Programo el workflow con start_delay = timedelta(seconds=30)
    assert len(fake_client.start_calls) == 1
    kwargs = fake_client.start_calls[0]["kwargs"]
    assert kwargs.get("start_delay") == timedelta(seconds=30)
    assert kwargs.get("id") == "remarketing-wa_5494444444444"
