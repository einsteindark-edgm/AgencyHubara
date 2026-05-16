"""Tests del use case `LoadOrStartSalesSession` con fakes.

Cubre los 4 caminos del legado:

1. Sin metadata previa -> arranca workflow Sales nuevo y le signal el mensaje.
2. Con sesion Sales corriendo -> reusa handle, signal directo (no start_workflow).
3. `active_route=remarketing` con handle vivo -> signalea Remarketing.
4. `active_route=remarketing` con handle muerto -> fallback a Sales (start o reuse).

Tambien valida que `phone_number_id` se persiste en metadata cuando llega.

PR-D global cleanup (ADR-2026-05-06-10): el `FakeBrainLoader` fue eliminado.
Tras la migracion DEHA workspace de Remarketing, `LoadOrStartSalesSession`
no carga `shared_brain/*.md` para ninguna ruta — `plugin_context` es siempre
`None`. Las assertions sobre `args[2]` esperan `None` en ambas rutas (Sales
y Remarketing).
"""
from __future__ import annotations

import pytest
from temporalio.client import WorkflowExecutionStatus

from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
    SALES_QUEUE,
)
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
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


class _Desc:
    def __init__(self, status) -> None:
        self.status = status


class FakeHandle:
    def __init__(
        self,
        status,
        raise_describe: Exception | None = None,
        raise_terminate: Exception | None = None,
    ) -> None:
        self._status = status
        self._raise_describe = raise_describe
        self._raise_terminate = raise_terminate
        self.signals: list[tuple[object, list]] = []
        self.terminate_reasons: list[str] = []
        self.id = None

    async def describe(self) -> _Desc:
        if self._raise_describe is not None:
            raise self._raise_describe
        return _Desc(self._status)

    async def signal(self, fn, *, args) -> None:
        self.signals.append((fn, args))

    async def terminate(self, reason: str = "") -> None:
        if self._raise_terminate is not None:
            raise self._raise_terminate
        self.terminate_reasons.append(reason)


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
) -> LoadOrStartSalesSession:
    """PR-D global cleanup (ADR-2026-05-06-10): el use case ya no recibe
    `sales_brain_loader`, `sales_brain_dir`, `remarketing_brain_loader`, ni
    `remarketing_brain_dir`.

    Tras la migracion DEHA workspace, ambas rutas (Sales y Remarketing) leen
    identidad/tono/catalogo desde su workspace canonico via `ContextBuilder`.
    El use case solo cablea client + metadata store.
    """
    async def factory():
        return client  # type: ignore[return-value]

    return LoadOrStartSalesSession(
        client_factory=factory,  # type: ignore[arg-type]
        metadata_store=metadata_store,  # type: ignore[arg-type]
    )


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_starts_new_sales_workflow_when_no_metadata():
    metadata = FakeMetadataStore(initial={})
    client = FakeClient()
    use_case = _make_use_case(metadata, client)

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
    # 3. Signal con el mensaje. PR-B: plugin_context = None para Sales — la
    #    identidad/tono/catalogo ahora viajan via workspace files, no via signal.
    started = client.started_handles["session-wa_42"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args == ["hola", None, None]


@pytest.mark.asyncio
async def test_reuses_running_sales_handle_without_starting_again():
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_VENTAS})
    existing = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(existing_handles={"session-wa_7": existing})
    use_case = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_7", message="hi", phone_number_id=None)

    # Sin phone_number_id no se escribe metadata.
    assert metadata.writes == []
    # No se arranco workflow nuevo.
    assert client.start_calls == []
    # El handle existente recibe el signal. PR-B: plugin_context = None.
    assert len(existing.signals) == 1
    fn, args = existing.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args == ["hi", None, None]


@pytest.mark.asyncio
async def test_routes_to_remarketing_when_active_and_running():
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    rem_handle = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(existing_handles={"remarketing-wa_9": rem_handle})
    use_case = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_9", message="vuelvo", phone_number_id=None)

    # No se arranca Sales workflow.
    assert client.start_calls == []
    # El handle de remarketing recibe el signal. PR-D global cleanup
    # (ADR-2026-05-06-10): plugin_context = None tambien para Remarketing —
    # la identidad/tono/catalogo viajan via `workspace/*.md` leidos por
    # `ContextBuilder` durante `build_prompt`.
    assert len(rem_handle.signals) == 1
    fn, args = rem_handle.signals[0]
    assert fn is RemarketingSessionWorkflow.send_message
    assert args == ["vuelvo", None, None]


@pytest.mark.asyncio
async def test_skips_dispatch_when_route_is_humano():
    """active_route=humano: el use case NO debe arrancar workflow ni signalear.

    El mensaje del cliente ya quedo en el JSONL (lo escribe
    `IngestInboundMessage.execute` antes de invocarnos). El humano lo lee
    desde el dashboard. Disparar al workflow reactivaria al LLM y pisaria
    la conversacion del humano — el bug que queremos evitar.
    """
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_HUMANO})
    client = FakeClient()
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_human_1", message="hola otra vez", phone_number_id=None
    )

    # No se arranca workflow ni se signalea ninguno.
    assert client.start_calls == []
    # phone_number_id era None, asi que no hubo writes.
    assert metadata.writes == []


@pytest.mark.asyncio
async def test_humano_route_still_persists_phone_number_id():
    """Aunque omitimos el dispatch, el `phone_number_id` nuevo se persiste
    para que el humano lo vea en el dashboard."""
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_HUMANO})
    client = FakeClient()
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_human_2",
        message="otra vez yo",
        phone_number_id="PID_HUMAN",
    )

    # phone_number_id se persistio.
    assert len(metadata.writes) == 1
    _, written = metadata.writes[0]
    assert written["phone_number_id"] == "PID_HUMAN"
    assert written["active_route"] == ROUTE_HUMANO
    # Pero nada de workflows.
    assert client.start_calls == []


@pytest.mark.asyncio
async def test_falls_back_to_sales_when_remarketing_handle_dead():
    """active_route=remarketing pero el handle reporta status != RUNNING -> Sales."""
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    dead_remarketing = FakeHandle(status=WorkflowExecutionStatus.COMPLETED)
    client = FakeClient(existing_handles={"remarketing-wa_3": dead_remarketing})
    use_case = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_3", message="hi again", phone_number_id=None)

    # Se arranca Sales workflow nuevo.
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["id"] == "session-wa_3"
    # El nuevo Sales handle recibe el signal. PR-B: plugin_context = None.
    started = client.started_handles["session-wa_3"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    assert fn is HubaraSalesSessionWorkflow.send_message
    assert args[2] is None
    # PR-D global cleanup (ADR-2026-05-06-10): el `remarketing_brain_loader`
    # ya no existe en el use case; el path Remarketing tambien se alimenta del
    # workspace canonico via `bootstrap_remarketing_session_activity`.
    # NO se signaleo al remarketing handle muerto.
    assert dead_remarketing.signals == []


@pytest.mark.asyncio
async def test_fallback_terminates_dead_remarketing_handle():
    """Fix 4 (H2): cuando hacemos fallback a Sales porque Remarketing no esta
    RUNNING, terminamos el handle zombie para que no se ejecute despues."""
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    dead_remarketing = FakeHandle(status=WorkflowExecutionStatus.COMPLETED)
    client = FakeClient(existing_handles={"remarketing-wa_zombie": dead_remarketing})
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_zombie", message="estoy aca", phone_number_id=None
    )

    # 1. Se llamo terminate() en el handle zombie con un reason significativo
    assert len(dead_remarketing.terminate_reasons) == 1
    assert "Sales fallback" in dead_remarketing.terminate_reasons[0]
    # 2. Sales se arranca igual que antes
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["id"] == "session-wa_zombie"


@pytest.mark.asyncio
async def test_fallback_terminate_failure_is_swallowed():
    """Fix 4: si terminate() del zombie falla (race, ya completed, etc), no
    rompemos el flujo — seguimos con el fallback a Sales."""
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_REMARKETING})
    # Handle que reporta COMPLETED y ademas lanza al terminate (doble race)
    dead = FakeHandle(
        status=WorkflowExecutionStatus.COMPLETED,
        raise_terminate=RuntimeError("workflow already completed"),
    )
    client = FakeClient(existing_handles={"remarketing-wa_4": dead})
    use_case = _make_use_case(metadata, client)

    # No debe raisear
    await use_case.execute(
        session_id="wa_4", message="seguimos", phone_number_id=None
    )

    # Sales arranca aunque el terminate haya fallado
    assert len(client.start_calls) == 1
