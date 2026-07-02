"""Tests del use case `LoadOrStartSalesSession` con fakes.

Cubre los 4 caminos del legado:

1. Sin metadata previa -> arranca workflow Sales nuevo y le signal el mensaje.
2. Con sesion Sales corriendo -> reusa handle, signal directo (no start_workflow).
3. `active_route=remarketing` con handle vivo -> signalea Remarketing.
4. `active_route=remarketing` con handle muerto -> fallback a Sales (start o reuse).

Tambien valida que `phone_number_id` se persiste en metadata cuando llega.

PR-D global cleanup (ADR-2026-05-06-10): el `FakeBrainLoader` fue eliminado.
Tras la migracion DEHA workspace de Remarketing, `LoadOrStartSalesSession`
no carga `shared_brain/*.md` para ninguna ruta. La ruta Remarketing manda
`plugin_context=None`. La ruta Sales inyecta el bloque de contexto de
Bogotá (`build_bogota_context_string()`) para que el LLM use el saludo
correcto segun la hora local de Colombia — las assertions de Sales validan
la forma de ese bloque, no el contenido literal (depende de `datetime.now`).
"""
from __future__ import annotations

import asyncio

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
)
from src.platform.plugin_manifest import get_task_queue

SALES_QUEUE = get_task_queue("chats", "sales")
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
# ADR-2026-05-20: the use_case dispatches by signal-name string ("send_message")
# now, no class references. We keep the imports of HubaraSalesSessionWorkflow
# only because the test still asserts that the *start_workflow* call references
# `HubaraSalesSessionWorkflow.run` (intra-agent ref, OK). RemarketingSessionWorkflow
# is no longer needed (cross-agent — replaced by string dispatch + manifest).


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

    async def start_workflow(
        self,
        workflow_run,
        payload,
        *,
        id,
        task_queue,
        start_signal=None,
        start_signal_args=None,
        id_conflict_policy=None,
    ):
        self.start_calls.append(
            {
                "workflow": workflow_run,
                "payload": payload,
                "id": id,
                "task_queue": task_queue,
                "start_signal": start_signal,
                "start_signal_args": start_signal_args,
                "id_conflict_policy": id_conflict_policy,
            }
        )
        # signal_with_start: si el workflow ya corre (id_conflict_policy
        # USE_EXISTING) entrega el signal al handle vivo y NO crea uno nuevo;
        # si no existe, arranca uno nuevo. Modela la semántica atómica real.
        if id in self.existing_handles:
            handle = self.existing_handles[id]
        else:
            handle = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
            handle.id = id
            self.started_handles[id] = handle
        if start_signal is not None:
            handle.signals.append((start_signal, list(start_signal_args or [])))
        return handle


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
    # 3. Signal con el mensaje. La identidad/tono/catalogo viajan via workspace
    #    files; `plugin_context` ahora trae el bloque de contexto de Bogotá
    #    (hora + saludo) — validamos la forma, no el contenido literal.
    started = client.started_handles["session-wa_42"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    # ADR-2026-05-20: signal dispatch is by NAME (string), not class method ref.
    assert fn == "send_message"
    assert args[0] == "hola"
    assert args[1] is None
    assert isinstance(args[2], list) and len(args[2]) == 1
    assert "Hora actual en Colombia" in args[2][0]


@pytest.mark.asyncio
async def test_reuses_running_sales_handle_without_starting_again():
    metadata = FakeMetadataStore(initial={"active_route": ROUTE_VENTAS})
    existing = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(existing_handles={"session-wa_7": existing})
    use_case = _make_use_case(metadata, client)

    await use_case.execute(session_id="wa_7", message="hi", phone_number_id=None)

    # Sin phone_number_id no se escribe metadata.
    assert metadata.writes == []
    # signal_with_start reusa el workflow vivo: NO crea una ejecución nueva
    # (started_handles vacío) aunque el call a start_workflow sí ocurre con
    # id_conflict_policy=USE_EXISTING.
    assert client.started_handles == {}
    assert len(client.start_calls) == 1
    # El handle existente recibe el mensaje (entregado por el start_signal).
    # Ruta Sales → plugin_context trae el bloque de contexto Bogotá.
    assert len(existing.signals) == 1
    fn, args = existing.signals[0]
    # ADR-2026-05-20: signal dispatch is by NAME (string), not class method ref.
    assert fn == "send_message"
    assert args[0] == "hi"
    assert args[1] is None
    assert isinstance(args[2], list) and len(args[2]) == 1
    assert "Hora actual en Colombia" in args[2][0]


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
    # ADR-2026-05-20: signal dispatch is by NAME (string) — no class ref needed.
    assert fn == "send_message"
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
    # El nuevo Sales handle recibe el signal. Ruta Sales → plugin_context trae
    # el bloque de contexto Bogotá; validamos la forma.
    started = client.started_handles["session-wa_3"]
    assert len(started.signals) == 1
    fn, args = started.signals[0]
    # ADR-2026-05-20: signal dispatch is by NAME (string), not class method ref.
    assert fn == "send_message"
    assert isinstance(args[2], list) and len(args[2]) == 1
    assert "Hora actual en Colombia" in args[2][0]
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


# ============================================================================
# Sesión c4e3416f: override remarketing→sales cuando hay Flow pendiente
# ============================================================================


@pytest.mark.asyncio
async def test_active_route_overridden_to_sales_when_flow_awaiting_recent():
    """ANTI-REGRESIÓN sesión c4e3416f: si hay un `nfm_reply` pendiente del
    WhatsApp Flow (<10 min desde el envío), el routing debe forzar Sales
    aunque metadata diga `active_route=remarketing`.

    Escenario: primer ghosting pre-Flow programó remarketing con
    start_delay=60s; remarketing arrancó + claim_routing pisó active_route;
    cuando llega el nfm_reply, sin este override iría a remarketing — que
    no tiene tools de venta ni contexto del pedido."""
    import time

    recent_ms = int(time.time() * 1000) - 60_000  # 1 min atrás
    metadata = FakeMetadataStore(initial={
        "active_route": ROUTE_REMARKETING,
        "shipping_flow_awaiting_reply_since_ms": recent_ms,
    })
    # No existe ni sales ni remarketing como running — el use case debe
    # arrancar un Sales nuevo.
    client = FakeClient()
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_flow_pending",
        message="[datos de envío recibidos] city=Bogotá; ...",
        phone_number_id=None,
    )

    # Sales arrancó (no remarketing).
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["id"] == "session-wa_flow_pending"


@pytest.mark.asyncio
async def test_no_override_when_flow_awaiting_flag_expired():
    """Si el flag tiene > 10 min de antigüedad, el override no aplica —
    seguimos respetando active_route=remarketing."""
    import time

    stale_ms = int(time.time() * 1000) - (11 * 60 * 1000)  # 11 min atrás
    metadata = FakeMetadataStore(initial={
        "active_route": ROUTE_REMARKETING,
        "shipping_flow_awaiting_reply_since_ms": stale_ms,
    })
    rem_handle = FakeHandle(status=WorkflowExecutionStatus.RUNNING)
    client = FakeClient(
        existing_handles={"remarketing-wa_stale": rem_handle},
    )
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_stale",
        message="vuelvo después",
        phone_number_id=None,
    )

    # Flag vencido → ruteo normal a remarketing (no override)
    assert client.start_calls == []
    assert len(rem_handle.signals) == 1


# ============================================================================
# Race de arranque (run 019f1b65 / sesión wa_573125671604): dos inbounds
# concurrentes sobre un workflow inexistente NO deben perder ningún signal.
# ============================================================================


class _RacingHandle:
    """Handle que acumula los signals entregados (start_signal o signal)."""

    def __init__(self, workflow_id: str) -> None:
        self.id = workflow_id
        self.signals: list[tuple[str, list]] = []

    async def describe(self):
        # Sólo se llama sobre handles YA arrancados en este fake.
        return _Desc(WorkflowExecutionStatus.RUNNING)

    async def signal(self, name, *, args) -> None:
        self.signals.append((name, args))


class RacingFakeClient:
    """Modela la semántica REAL de Temporal para el arranque de workflows.

    * `get_workflow_handle` sobre un id inexistente → `describe()` lanza (el
      workflow no existe todavía).
    * `start_workflow` **sin** `start_signal` sobre un id ya arrancado →
      `WorkflowAlreadyStartedError` (reproduce la race: el 2º ingest
      concurrente pierde su signal con el arranque manual).
    * `start_workflow` **con** `start_signal` + `id_conflict_policy=USE_EXISTING`
      → atómico: si no existe lo crea y entrega el `start_signal`; si ya existe,
      entrega el signal al handle vivo. Ningún mensaje se pierde.

    Los `await asyncio.sleep(0)` fuerzan el interleaving determinista de los
    dos ingests (ambos pasan `describe` antes de que cualquiera arranque).
    """

    def __init__(self) -> None:
        self.started: dict[str, _RacingHandle] = {}

    def get_workflow_handle(self, workflow_id: str):
        if workflow_id in self.started:
            return self.started[workflow_id]

        async def _raise_describe():
            await asyncio.sleep(0)
            raise RuntimeError("workflow not found")

        h = _RacingHandle(workflow_id)
        h.describe = _raise_describe  # type: ignore[method-assign]
        return h

    async def start_workflow(
        self,
        workflow_run,
        payload,
        *,
        id,
        task_queue,
        start_signal=None,
        start_signal_args=None,
        id_conflict_policy=None,
    ):
        await asyncio.sleep(0)
        if start_signal is None:
            # Camino manual (código actual): choca si el id ya existe.
            if id in self.started:
                raise WorkflowAlreadyStartedError(id, "HubaraSalesSessionWorkflow")
            h = _RacingHandle(id)
            self.started[id] = h
            return h
        # signal_with_start atómico: crear-o-reusar + entregar el signal.
        h = self.started.get(id) or _RacingHandle(id)
        self.started[id] = h
        h.signals.append((start_signal, list(start_signal_args or [])))
        return h


@pytest.mark.asyncio
async def test_concurrent_first_contact_delivers_both_messages():
    """RED (race run 019f1b65): el cliente manda 2 mensajes seguidos cuando
    NO hay workflow vivo. Los dos ingests corren concurrentes; ambos ven
    'workflow inexistente' y ambos intentan arrancarlo. Con el arranque manual
    (get→describe→start→signal), el 2º `start_workflow` choca con
    `WorkflowAlreadyStartedError` y su mensaje NUNCA llega al workflow (el LLM
    sólo ve uno). El fix (`signal_with_start` atómico) garantiza que los DOS
    mensajes lleguen a la cola de signals."""
    metadata = FakeMetadataStore(initial={})
    client = RacingFakeClient()
    use_case = _make_use_case(metadata, client)

    await asyncio.gather(
        use_case.execute(session_id="wa_race", message="Hola", phone_number_id="PID"),
        use_case.execute(
            session_id="wa_race", message="quiero el difusor", phone_number_id="PID"
        ),
    )

    handle = client.started["session-wa_race"]
    delivered = {args[0] for _name, args in handle.signals}
    assert delivered == {"Hola", "quiero el difusor"}, (
        f"se perdió un mensaje en la ráfaga: llegaron {delivered}"
    )


@pytest.mark.asyncio
async def test_no_override_when_active_route_is_ventas():
    """Si active_route ya es ventas, el flag no cambia el comportamiento
    (defensa: el override solo aplica para REMARKETING)."""
    import time

    recent_ms = int(time.time() * 1000) - 60_000
    metadata = FakeMetadataStore(initial={
        "active_route": ROUTE_VENTAS,
        "shipping_flow_awaiting_reply_since_ms": recent_ms,
    })
    client = FakeClient()
    use_case = _make_use_case(metadata, client)

    await use_case.execute(
        session_id="wa_sales_flag",
        message="datos",
        phone_number_id=None,
    )

    # Sales arrancó normal (sin override — ya era sales)
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["id"] == "session-wa_sales_flag"
