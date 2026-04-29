"""Tests del use case `LoadOrStartSalesSession` con fakes.

Cubre los 4 caminos del legado:

1. Sin metadata previa -> arranca workflow Sales nuevo y le signal el mensaje.
2. Con sesion Sales corriendo -> reusa handle, signal directo (no start_workflow).
3. `active_route=remarketing` con handle vivo -> signalea Remarketing.
4. `active_route=remarketing` con handle muerto -> fallback a Sales (start o reuse).

Tambien valida que `phone_number_id` se persiste en metadata cuando llega.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowExecutionStatus

from src.core.constants import ROUTE_REMARKETING, ROUTE_VENTAS, SALES_QUEUE
from src.domains.sales_whatsapp.application.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.domains.sales_whatsapp.contracts import SalesSessionInput
from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
from src.domains.remarketing_whatsapp.workflows.remarketing import (
    RemarketingSessionWorkflow,
)


# --- Fakes -----------------------------------------------------------------


class FakeMetadataStore:
    def __init__(self, initial: dict | None = None) -> None:
        self.data = dict(initial or {})
        self.writes: list[tuple[str, dict]] = []

    def read(self, session_id: str) -> dict:
        return dict(self.data)

    def write(self, session_id: str, data: dict) -> None:
        self.writes.append((session_id, dict(data)))
        self.data = dict(data)


class FakeBrainLoader:
    def __init__(self, payload: list[str]) -> None:
        self._payload = payload
        self.calls: list[Path] = []

    def load(
        self,
        brain_dir: Path,
        files: tuple[str, ...] = ("identity.md", "knowledge.md", "instructions.md"),
    ) -> list[str]:
        self.calls.append(brain_dir)
        return list(self._payload)


class _Desc:
    def __init__(self, status) -> None:
        self.status = status


class FakeHandle:
    def __init__(self, status, raise_describe: Exception | None = None) -> None:
        self._status = status
        self._raise_describe = raise_describe
        self.signals: list[tuple[object, list]] = []
        self.id = None

    async def describe(self) -> _Desc:
        if self._raise_describe is not None:
            raise self._raise_describe
        return _Desc(self._status)

    async def signal(self, fn, *, args) -> None:
        self.signals.append((fn, args))


class FakeClient:
    """Fake client que provee `get_workflow_handle` y `start_workflow`.

    `existing_handles[workflow_id]` define que devuelve `get_workflow_handle`.
    Si no esta, el get devuelve un FakeHandle que lanza RPCError en describe()
    (simula 'workflow no existe').
    `start_workflow` registra la llamada y devuelve un nuevo handle 'recien arrancado'.
    """

    def __init__(self, existing_handles: dict[str, FakeHandle] | None = None) -> None:
        self.existing_handles: dict[str, FakeHandle] = dict(existing_handles or {})
        self.start_calls: list[dict] = []
        self.started_handles: dict[str, FakeHandle] = {}

    def get_workflow_handle(self, workflow_id: str) -> FakeHandle:
        if workflow_id in self.existing_handles:
            return self.existing_handles[workflow_id]
        # describe() raises -> the use case enters except branch and
        # falls back to start_workflow / fallback to sales (depending on path).
        return FakeHandle(
            status=None,
            raise_describe=RuntimeError("workflow does not exist"),
        )

    async def start_workflow(self, workflow_run, payload, *, id, task_queue):
        self.start_calls.append(
            {
                "workflow": workflow_run,
                "payload": payload,
                "id": id,
                "task_queue": task_queue,
            }
        )
        new_handle = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
        new_handle.id = id
        self.started_handles[id] = new_handle
        return new_handle


# --- Helpers ---------------------------------------------------------------


def _make_use_case(
    metadata_store: FakeMetadataStore,
    client: FakeClient,
    sales_brain: list[str] | None = None,
    remarketing_brain: list[str] | None = None,
) -> tuple[LoadOrStartSalesSession, FakeBrainLoader, FakeBrainLoader]:
    sales_loader = FakeBrainLoader(sales_brain or ["sales-brain"])
    rem_loader = FakeBrainLoader(remarketing_brain or ["rem-brain"])

    async def factory():
        return client  # type: ignore[return-value]

    use_case = LoadOrStartSalesSession(
        client_factory=factory,  # type: ignore[arg-type]
        metadata_store=metadata_store,  # type: ignore[arg-type]
        sales_brain_loader=sales_loader,  # type: ignore[arg-type]
        remarketing_brain_loader=rem_loader,  # type: ignore[arg-type]
        sales_brain_dir=Path("/tmp/sales-brain"),
        remarketing_brain_dir=Path("/tmp/rem-brain"),
    )
    return use_case, sales_loader, rem_loader


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_starts_new_sales_workflow_when_no_metadata():
    metadata = FakeMetadataStore(initial={})
    client = FakeClient()
    use_case, sales_loader, rem_loader = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_42", message="hola", phone_number_id="PID"
    )

    # 1. Phone number id se persiste en metadata.
    assert metadata.writes == [("wa_42", {"phone_number_id": "PID"})]
    # 2. Se intenta arrancar HubaraSalesSessionWorkflow.
    assert len(client.start_calls) == 1
    call = client.start_calls[0]
    assert call["id"] == "session-wa_42"
    assert call["task_queue"] == SALES_QUEUE
    assert isinstance(call["payload"], SalesSessionInput)
    assert call["payload"].session_id == "wa_42"
    # 3. Signal con el mensaje + el sales brain context.
    started = client.started_handles["session-wa_42"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args == ["hola", None, ["sales-brain"]]
    # 4. Solo el sales brain loader fue invocado.
    assert sales_loader.calls == [Path("/tmp/sales-brain")]
    assert rem_loader.calls == []


@pytest.mark.asyncio
async def test_reuses_running_sales_handle_without_starting_again():
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_VENTAS})
    existing = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(existing_handles={"session-wa_7": existing})
    use_case, _, _ = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_7", message="hi", phone_number_id=None)

    # Sin phone_number_id no se escribe metadata.
    assert metadata.writes == []
    # No se arranco workflow nuevo.
    assert client.start_calls == []
    # El handle existente recibe el signal.
    assert len(existing.signals) == 1
    fn, args = existing.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args == ["hi", None, ["sales-brain"]]


@pytest.mark.asyncio
async def test_routes_to_remarketing_when_active_and_running():
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    rem_handle = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(existing_handles={"remarketing-wa_9": rem_handle})
    use_case, sales_loader, rem_loader = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_9", message="vuelvo", phone_number_id=None)

    # No se arranca Sales workflow.
    assert client.start_calls == []
    # El handle de remarketing recibe el signal con el remarketing brain.
    assert len(rem_handle.signals) == 1
    fn, args = rem_handle.signals[0]
    assert fn is RemarketingSessionWorkflow.send_message
    assert args == ["vuelvo", None, ["rem-brain"]]
    # Solo el remarketing brain loader fue invocado.
    assert rem_loader.calls == [Path("/tmp/rem-brain")]
    assert sales_loader.calls == []


@pytest.mark.asyncio
async def test_falls_back_to_sales_when_remarketing_handle_dead():
    """active_route=remarketing pero el handle reporta status != RUNNING -> Sales."""
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    dead_remarketing = FakeHandle(status=WorkflowExecutionStatus.COMPLETED)
    client = FakeClient(existing_handles={"remarketing-wa_3": dead_remarketing})
    use_case, sales_loader, rem_loader = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_3", message="hi again", phone_number_id=None)

    # Remarketing brain se cargo (intento) y luego fallback.
    assert rem_loader.calls == [Path("/tmp/rem-brain")]
    # Se arranca Sales workflow nuevo.
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["id"] == "session-wa_3"
    # El nuevo Sales handle recibe el signal con el sales brain.
    started = client.started_handles["session-wa_3"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args[2] == ["sales-brain"]
    # Sales brain loader tambien se invoco (el fallback recarga).
    assert sales_loader.calls == [Path("/tmp/sales-brain")]
    # NO se signaleo al remarketing handle muerto.
    assert dead_remarketing.signals == []
