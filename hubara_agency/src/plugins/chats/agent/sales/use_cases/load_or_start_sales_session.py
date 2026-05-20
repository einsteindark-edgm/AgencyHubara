"""Use case: decide la ruta activa (Sales vs Remarketing) y se asegura de que
exista un workflow handle vivo, signalandole el mensaje del usuario.

Equivalente al legado `service._signal_temporal_and_poll`. La diferencia es
que aqui:

* `metadata.json` se accede via ``FilesystemMetadataStore`` (no
  ``Path.read_text`` directo).
* La conexion al cluster Temporal entra como `client_factory` async
  (idealmente `get_temporal_client`).

PR-D (Sales): el `BrainLoaderPort` (y sus `brain_dir`) se eliminaron del path
Sales. La identidad / tono / catalogo de Sales viven en `workspace/{IDENTITY,
SOUL,USER,TOOLS,AGENTS}.md` y `workspace/skills/hubara_catalog/SKILL.md`,
leidos por `ContextBuilder.build_system_prompt` durante `build_prompt`.

PR-D (global cleanup, ADR-2026-05-06-10): tras la migracion DEHA workspace
de Remarketing (PR-A/PR-B remarketing), `RemarketingSessionWorkflow` lee la
identidad desde su propio workspace canonico via `ContextBuilder`. Este use
case ya no carga `shared_brain/*.md` — `plugin_context` para la ruta
Remarketing ahora es `None` igual que en Sales. Los fields
`remarketing_brain_loader` y `remarketing_brain_dir` del constructor se
eliminaron; no quedan callers de `BrainLoaderPort` en el repo.

PR-E: el `MetadataStorePort` Protocol intermedio se elimino. El use case
ahora type-hints la concreta ``FilesystemMetadataStore`` directo (Python
sigue siendo duck-typed, los fakes en tests pasan sin isinstance check).

Esto permite testear el use case con fakes sin levantar Temporal y sin tocar
filesystem real (NEW-2 cerrado).

Deuda consciente (R-DIP parcial): este use case importa `temporalio.client`
y los workflow classes directamente. El alcance de F9 acepta esto (la tarea
dice explicitamente "NO introduce activities nuevas; las llamadas a Temporal
siguen siendo client.start_workflow / handle.signal desde el handler HTTP").
Un futuro PR podria abstraer un `WorkflowDispatcherPort` con
`get_handle / start / signal` para invertir esta dependencia 100%.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import structlog
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from exoclaw_temporal.config import WorkspaceConfig

from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
)
from src.platform.plugin_manifest import get_task_queue
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow

logger = structlog.get_logger()


ClientFactory = Callable[[], Awaitable[Client]]


class LoadOrStartSalesSession:
    """Resuelve la ruta del mensaje y asegura que el workflow correcto este corriendo.

    Reglas (heredadas del legado):

    * `metadata.active_route == "remarketing"`:  intentar reusar
      `remarketing-{session_id}`. Si existe y esta RUNNING, signalear con
      `plugin_context=None` — PR-D global cleanup (ADR-2026-05-06-10):
      `RemarketingSessionWorkflow` lee identidad / tono / catalogo desde su
      workspace canonico via `ContextBuilder`. Si esta muerto, fallback a Sales.
    * Caso contrario (default `ventas`): reusar `session-{session_id}` o
      `start_workflow(HubaraSalesSessionWorkflow.run, SalesSessionInput(...))`.
      Se signala con `plugin_context=None` — el tercer arg ya no carga
      identidad/catalogo (vienen del workspace canonico de cada agente).
      Sobrevive en la signature como hueco para datos volatiles del turno
      (A-MEM, snippets) — ver `core/workflow_helpers.py:PendingMessage`.

    PR-D global cleanup (ADR-2026-05-06-10): los args / fields
    `remarketing_brain_loader` y `remarketing_brain_dir` se eliminaron. Tras
    la migracion DEHA workspace de Remarketing (PR-A/PR-B), nadie consume
    `BrainLoaderPort` ni `shared_brain/*.md`.

    PR-E: ``metadata_store`` ahora se type-hints como la concreta
    ``FilesystemMetadataStore`` (no ``MetadataStorePort``). Los fakes en tests
    siguen funcionando porque Python es duck-typed; un fake con
    ``read(...)`` / ``write(...)`` se acepta sin problemas.
    """

    def __init__(
        self,
        client_factory: ClientFactory,
        metadata_store: FilesystemMetadataStore,
        sales_runtime_workspace: WorkspaceConfig | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._metadata_store = metadata_store
        # PR-B/PR-D: el WorkspaceConfig canonico del agente Sales se propaga al
        # `bootstrap_sales_session_activity` via `SalesSessionInput.runtime_workspace_path`.
        # Es el unico canal por el que la identidad / tono / catalogo entran al workflow.
        self._sales_runtime_workspace = sales_runtime_workspace

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
    ) -> None:
        # 1. Resolver ruta y persistir phone_number_id (lectura + posible escritura).
        data = self._metadata_store.read(session_id)
        active_route = data.get("active_route", ROUTE_VENTAS)

        if phone_number_id:
            data["phone_number_id"] = phone_number_id
            self._metadata_store.write(session_id, data)

        # 1.5. Ruta humano: cliente esta en el inbox humano (escalation previa).
        # El mensaje del cliente ya quedo persistido en el JSONL (lo hace
        # `IngestInboundMessage.execute` antes de invocarnos), asi que el humano
        # lo lee desde el dashboard. NO arrancamos workflow ni signaleamos —
        # eso reactivaria al LLM y pisaria la conversacion del humano.
        if active_route == ROUTE_HUMANO:
            logger.info(
                "Session in HUMANO route — message logged only, no LLM dispatch",
                session_id=session_id,
            )
            return

        # 2. Conectar al cluster.
        client = await self._client_factory()

        # 3. Si la ruta activa es remarketing, intentamos reusar; si murio, fallback.
        if active_route == ROUTE_REMARKETING:
            workflow_id = f"remarketing-{session_id}"
            logger.info(
                "Routing webhook to Remarketing Agent", workflow_id=workflow_id
            )
            # ADR-2026-05-20: NO importamos `RemarketingSessionWorkflow`.
            # Este branch SOLO reusa un workflow_id existente; si murió,
            # fallback a Sales. No arrancamos un Remarketing nuevo desde acá
            # (eso lo hace el dispatcher declarativo cuando Sales emite
            # `SalesSessionCompletionEvent(tag=INTERESADO)`). La signal usa
            # el nombre del handler directamente — Temporal acepta
            # `handle.signal("send_message", args=...)` sin la referencia de
            # la clase del workflow. Esto elimina la violación R-DIP #10
            # documentada en .importlinter.
            # PR-D global cleanup (ADR-2026-05-06-10): tras la migracion DEHA
            # workspace de Remarketing (PR-A/PR-B), `RemarketingSessionWorkflow`
            # lee identidad / tono / catalogo desde su workspace canonico via
            # `ContextBuilder`. El `shared_brain/*.md` legacy ya no se carga,
            # asi que `plugin_context` para la ruta Remarketing es `None` igual
            # que en Sales. El field se conserva en el signal por compatibilidad
            # de fixture/replay y como hueco para datos volatiles del turno
            # (ver `core/workflow_helpers.py:PendingMessage`).
            plugin_context = None

            try:
                handle = client.get_workflow_handle(workflow_id)
                desc = await handle.describe()
                if desc.status != WorkflowExecutionStatus.RUNNING:
                    raise RuntimeError("Remarketing workflow is no longer running")
            except (RPCError, RuntimeError):
                # Fix 4 (H2): si el handle de Remarketing existe pero no esta
                # RUNNING (terminado, fallado, o jamas se arranco), lo
                # terminamos defensivamente antes de hacer fallback. Esto
                # previene la colision donde:
                #   1. Sales etiqueta INTERESADO → programa Remarketing
                #      con `start_delay`.
                #   2. Cliente responde antes que expire el delay.
                #   3. Webhook ve `active_route=remarketing` pero Remarketing
                #      "todavia no esta RUNNING" → fallback a Sales (este
                #      branch). Sin terminate, Remarketing se dispara despues
                #      y manda un saludo zombie.
                # El terminate es best-effort; si el handle no existe o esta
                # en estado terminal, los excepts de abajo lo absorben.
                try:
                    zombie = client.get_workflow_handle(workflow_id)
                    await zombie.terminate(
                        reason="Cliente re-engaged via Sales fallback, cancelando remarketing programado"
                    )
                    logger.info(
                        "Terminated stale Remarketing handle on Sales fallback",
                        workflow_id=workflow_id,
                    )
                except Exception:
                    # No existe / ya terminado / race. No es error; seguimos.
                    pass
                logger.warning(
                    "Remarketing workflow not found or finished, falling back to Sales"
                )
                active_route = ROUTE_VENTAS

        # 4. Caso default (o fallback): rutear a Sales.
        if active_route != ROUTE_REMARKETING:
            workflow_id = f"session-{session_id}"
            logger.info("Routing webhook to Sales Agent", workflow_id=workflow_id)
            # Sales workflow class import is intra-agent (sales/use_cases →
            # sales/workflows), so we keep `HubaraSalesSessionWorkflow.run` as
            # a typed reference — that's NOT a R-DIP #10 issue (same agent).
            # PR-B: el plugin_context legacy (shared_brain/*.md) deja de viajar
            # por el signal del path Sales. La identidad/tono/catalogo ahora
            # viven en `workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md` y
            # `workspace/skills/hubara_catalog/SKILL.md`, leidos por
            # `ContextBuilder.build_system_prompt` durante `build_prompt`.
            # PR-D: el campo `plugin_context` sobrevive en la signature del
            # signal y en `PendingMessage` como hueco para datos volatiles del
            # turno (A-MEM, snippets retrieved). Documentado en
            # `core/workflow_helpers.py:PendingMessage`.
            plugin_context = None

            try:
                handle = client.get_workflow_handle(workflow_id)
                desc = await handle.describe()
                if desc.status != WorkflowExecutionStatus.RUNNING:
                    raise RuntimeError("Workflow is no longer running")
            except (RPCError, RuntimeError):
                logger.info(
                    "Creando HubaraSalesSessionWorkflow", workflow_id=workflow_id
                )
                # F7: el caller ya no construye SessionInput. El workflow lo arma
                # via `bootstrap_sales_session_activity` como primera activity.
                # PR-A/PR-B: `runtime_workspace_path` se inyecta para que el
                # bootstrap activity instancie `WorkspaceConfig(path=...)` desde
                # el workspace canonico del agente.
                runtime_path = (
                    self._sales_runtime_workspace.path
                    if self._sales_runtime_workspace is not None
                    else None
                )
                handle = await client.start_workflow(
                    HubaraSalesSessionWorkflow.run,
                    SalesSessionInput(
                        session_id=session_id,
                        runtime_workspace_path=runtime_path,
                    ),
                    id=workflow_id,
                    task_queue=get_task_queue("chats", "sales"),
                )

        # 5. Signal del mensaje al workflow seleccionado.
        #
        # ADR-2026-05-20: signal por NOMBRE (string). Temporal acepta
        # `handle.signal("send_message", args=...)` además de la referencia
        # de método. Esto desacopla del workflow class — el path remarketing
        # ya no necesita el import cross-agent, y el path sales sigue siendo
        # equivalente al `HubaraSalesSessionWorkflow.send_message` que usaba
        # antes (mismo nombre de @workflow.signal).
        await handle.signal(
            "send_message",
            args=[message, None, plugin_context],
        )
