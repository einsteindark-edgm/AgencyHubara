"""Tests de los helpers `start_remarketing_for_session` y
`terminate_session_workflows` (extraídos del dispatcher en el PR de handoff).

Cubren los caminos defensivos pre-mortem:
  - terminate falla por uno → sigue con el otro.
  - start_workflow race con WorkflowAlreadyStartedError → swallow.
  - terminate race entre describe y terminate → swallow + sigue.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from src.platform.temporal.dispatcher import (
    start_remarketing_for_session,
    terminate_session_workflows,
)


class _Desc:
    def __init__(self, status) -> None:
        self.status = status


class _Handle:
    """Fake workflow handle con describe/terminate mockeables independientemente."""

    def __init__(
        self,
        *,
        describe_status=WorkflowExecutionStatus.RUNNING,
        describe_raises: Exception | None = None,
        terminate_raises: Exception | None = None,
    ) -> None:
        self._describe_status = describe_status
        self._describe_raises = describe_raises
        self._terminate_raises = terminate_raises
        self.terminated = False

    async def describe(self) -> _Desc:
        if self._describe_raises is not None:
            raise self._describe_raises
        return _Desc(self._describe_status)

    async def terminate(self, reason: str | None = None) -> None:
        if self._terminate_raises is not None:
            raise self._terminate_raises
        self.terminated = True


class _Client:
    """Fake Temporal client. `handles` mapea workflow_id → _Handle."""

    def __init__(self, handles: dict[str, _Handle] | None = None) -> None:
        self.handles = handles or {}
        self.start_calls: list[dict[str, Any]] = []
        self.start_raises: Exception | None = None

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        if workflow_id in self.handles:
            return self.handles[workflow_id]
        # No existe: simulamos con RPCError NOT_FOUND
        h = _Handle(
            describe_raises=RPCError(
                "workflow not found", RPCStatusCode.NOT_FOUND, b""
            ),
        )
        return h

    async def start_workflow(self, *args, **kwargs):
        self.start_calls.append({"args": args, "kwargs": kwargs})
        if self.start_raises is not None:
            raise self.start_raises
        return MagicMock()


# ---------- terminate_session_workflows ----------


@pytest.mark.asyncio
async def test_terminate_both_running_workflows():
    """Caminata feliz: ambos workflows RUNNING → ambos terminados."""
    client = _Client(
        handles={
            "session-wa_1": _Handle(),
            "remarketing-wa_1": _Handle(),
        }
    )
    terminated = await terminate_session_workflows(client, "wa_1")
    assert terminated == ["session-wa_1", "remarketing-wa_1"]
    assert client.handles["session-wa_1"].terminated is True
    assert client.handles["remarketing-wa_1"].terminated is True


@pytest.mark.asyncio
async def test_terminate_skips_completed_workflows():
    """Si describe reporta != RUNNING, no llamamos terminate."""
    client = _Client(
        handles={
            "session-wa_1": _Handle(
                describe_status=WorkflowExecutionStatus.COMPLETED,
            ),
        }
    )
    terminated = await terminate_session_workflows(client, "wa_1")
    assert terminated == []
    assert client.handles["session-wa_1"].terminated is False


@pytest.mark.asyncio
async def test_terminate_continues_after_individual_failure():
    """Si el primer terminate explota (race), no debe bloquear el segundo.

    Este es el fix pre-mortem clave: antes catcheaba solo RPCError; ahora
    catchea cualquier excepción para que un fallo individual no rompa el
    endpoint completo."""
    client = _Client(
        handles={
            "session-wa_1": _Handle(
                terminate_raises=RuntimeError("simulada race describe-terminate"),
            ),
            "remarketing-wa_1": _Handle(),
        }
    )
    terminated = await terminate_session_workflows(client, "wa_1")
    # session-wa_1 explotó pero remarketing-wa_1 sí se terminó.
    assert terminated == ["remarketing-wa_1"]
    assert client.handles["remarketing-wa_1"].terminated is True


@pytest.mark.asyncio
async def test_terminate_handles_describe_failure_gracefully():
    """describe() fallando (RPCError + otros) no debe bloquear el siguiente."""
    client = _Client(
        handles={
            "session-wa_1": _Handle(
                describe_raises=RuntimeError("transient cluster error"),
            ),
            "remarketing-wa_1": _Handle(),
        }
    )
    terminated = await terminate_session_workflows(client, "wa_1")
    assert terminated == ["remarketing-wa_1"]


# ---------- start_remarketing_for_session ----------


@pytest.mark.asyncio
async def test_start_remarketing_arranca_limpio_sin_previo():
    """Sin zombie previo: get_workflow_handle devuelve un handle que RPCError
    NOT_FOUND en describe; el helper sigue al start_workflow."""
    client = _Client()
    await start_remarketing_for_session(
        client,
        session_id="wa_new",
        motivo="cliente quedó indeciso",
    )
    assert len(client.start_calls) == 1
    call = client.start_calls[0]
    assert call["kwargs"]["id"] == "remarketing-wa_new"


@pytest.mark.asyncio
async def test_start_remarketing_termina_zombie_previo():
    """Con zombie RUNNING, lo terminamos antes de arrancar el nuevo."""
    zombie = _Handle()
    client = _Client(handles={"remarketing-wa_z": zombie})
    await start_remarketing_for_session(
        client,
        session_id="wa_z",
        motivo="zombie test",
    )
    assert zombie.terminated is True
    assert len(client.start_calls) == 1


@pytest.mark.asyncio
async def test_start_remarketing_swallows_terminate_race():
    """Si el zombie reporta RUNNING pero terminate falla (race a COMPLETED
    entre describe y terminate), igualmente arrancamos el nuevo workflow."""
    zombie = _Handle(
        terminate_raises=RPCError(
            "workflow already closed", RPCStatusCode.NOT_FOUND, b""
        ),
    )
    client = _Client(handles={"remarketing-wa_r": zombie})
    await start_remarketing_for_session(
        client,
        session_id="wa_r",
        motivo="race test",
    )
    # No explotó. start_workflow corrió.
    assert len(client.start_calls) == 1


@pytest.mark.asyncio
async def test_start_remarketing_swallows_already_started_race():
    """Si entre nuestro terminate y start_workflow otro caller arrancó uno,
    swallow WorkflowAlreadyStartedError — la sesión está cubierta."""
    client = _Client()
    client.start_raises = WorkflowAlreadyStartedError(
        "remarketing-wa_q", "RemarketingWorkflow"
    )
    # NO debería explotar.
    await start_remarketing_for_session(
        client,
        session_id="wa_q",
        motivo="already started race",
    )
    # Llamamos start_workflow una vez aunque haya explotado.
    assert len(client.start_calls) == 1
