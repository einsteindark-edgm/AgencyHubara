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

from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision


def test_dispatcher_module_exports_two_activities() -> None:
    import src.platform.temporal.dispatcher as mod
    # Ambas funciones existen y son corutinas
    assert inspect.iscoroutinefunction(mod.start_or_signal_sales_workflow_activity)
    assert inspect.iscoroutinefunction(mod.schedule_remarketing_workflow_activity)


class _FakeHandle:
    def __init__(self, describe_status: str = "RUNNING", describe_raises: bool = False) -> None:
        self.signaled_with: list[tuple[Any, ...]] = []
        self.terminated_with_reason: str | None = None
        self._describe_status = describe_status
        self._describe_raises = describe_raises

    async def describe(self) -> Any:
        if self._describe_raises:
            from temporalio.service import RPCError, RPCStatusCode
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
        from temporalio.client import WorkflowExecutionStatus
        class _D:
            status = WorkflowExecutionStatus.RUNNING if self._describe_status == "RUNNING" else WorkflowExecutionStatus.COMPLETED
        return _D()

    async def signal(self, *args: Any, **kwargs: Any) -> None:
        self.signaled_with.append((args, kwargs))

    async def terminate(self, reason: str = "") -> None:
        self.terminated_with_reason = reason


class _FakeClient:
    def __init__(self, handle: _FakeHandle | None = None) -> None:
        self.start_calls: list[dict] = []
        self.handle = handle if handle is not None else _FakeHandle()

    def get_workflow_handle(self, wf_id: str) -> _FakeHandle:
        self.handle.workflow_id = wf_id
        return self.handle

    async def start_workflow(self, *args: Any, **kwargs: Any) -> _FakeHandle:
        self.start_calls.append({"args": args, "kwargs": kwargs})
        return self.handle


async def test_start_or_signal_sales_writes_handoff_to_metadata_no_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Fix 3: el dispatcher YA NO signala con `[SISTEMA INTERNO]: ...`.

    En su lugar escribe `pending_handoff_summary` en metadata.json y el
    workflow Sales lo lee via `read_and_clear_pending_handoff_activity`.
    """
    fake_client = _FakeClient()

    async def fake_get_client() -> _FakeClient:
        return fake_client

    monkeypatch.setattr(
        "src.platform.temporal.dispatcher.get_temporal_client",
        fake_get_client,
    )
    # Redirigir WORKSPACE_VAULT_DIR del modulo a un tmp para no pisar nada real
    monkeypatch.setattr(
        "src.platform.temporal.dispatcher.WORKSPACE_VAULT_DIR",
        tmp_path,
    )

    from src.platform.temporal.dispatcher import (
        start_or_signal_sales_workflow_activity,
    )

    decision = TransferDecision(
        session_id="wa_5491111111111",
        target_route="ventas",
        summary="quiere ver el catalogo",
    )

    env = ActivityEnvironment()
    await env.run(start_or_signal_sales_workflow_activity, decision)

    # 1. No se llama start_workflow porque describe() devolvio RUNNING
    assert fake_client.start_calls == []
    # 2. NO se manda signal — el flujo legacy `[SISTEMA INTERNO]: ...` se elimino
    assert fake_client.handle.signaled_with == []
    # 3. Pero SI se escribio el handoff en metadata
    import json
    metadata_file = tmp_path / "wa_5491111111111" / "metadata.json"
    assert metadata_file.exists()
    data = json.loads(metadata_file.read_text())
    assert data["pending_handoff_summary"] == "quiere ver el catalogo"


async def test_schedule_remarketing_uses_start_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    # Caso "no zombie": describe() lanza RPCError (workflow no existe), el
    # dispatcher arranca el remarketing limpio.
    fake_client = _FakeClient(handle=_FakeHandle(describe_raises=True))

    async def fake_get_client() -> _FakeClient:
        return fake_client

    monkeypatch.setattr(
        "src.platform.temporal.dispatcher.get_temporal_client",
        fake_get_client,
    )
    from src.platform.temporal.dispatcher import (
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
