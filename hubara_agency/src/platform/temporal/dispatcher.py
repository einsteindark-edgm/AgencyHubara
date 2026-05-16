"""Activities-dispatcher que ejecutan los efectos durables que antes vivian
dentro de las tools (ADR-001).

Antes: la tool abria `temporal_client` y hacia `start_workflow`/`signal` desde
dentro del cuerpo de `execute_tool`. Eso rompe el arbol de durabilidad.

Ahora: las tools devuelven un payload de decision (`TransferDecision`,
`ScheduleRemarketingDecision`); el workflow lo lee y llama estas activities.
Temporal aplica retry y replay sobre la activity, no sobre la tool.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import SALES_QUEUE, REMARKETING_QUEUE
from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision
from src.platform.state import FilesystemMetadataStore
from src.platform.temporal.client import get_temporal_client


@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None:
    """Transfiere control a Sales: escribe handoff en metadata + asegura Sales corriendo.

    Reemplaza al `send_message` sintetico `[SISTEMA INTERNO]: ...` que viajaba
    como signal y se procesaba como turno de usuario aparte (causa root del
    bug double-reply, run b9639be1). Ahora el contexto del handoff vive en
    `metadata.pending_handoff_summary` y el workflow Sales lo lee via
    `read_and_clear_pending_handoff_activity` — primero al iniciar (cold) y
    luego al inicio de cada iteracion del loop (warm). El coalesce lo mueve
    a `plugin_context` para que NO ensucie el rol "user" de la conversacion.
    """
    # Imports locales para evitar ciclos: el workflow importa este modulo a traves
    # de `imports_passed_through`, y workflows no deben importarse mutuamente.
    from src.plugins.chats.agent.sales.config.env import get_workspace_path
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow

    session_id = decision.session_id
    summary = decision.summary or "El cliente volvió a interactuar"

    # 1. Persistir contexto de handoff en metadata. Atomic via tmpfile rename
    #    en FilesystemMetadataStore (POSIX-atomic). El workflow Sales lo
    #    consumira en el proximo turno (cold via bootstrap, warm via refresh
    #    per-iteration).
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    data = metadata_store.read(session_id)
    data["pending_handoff_summary"] = summary
    metadata_store.write(session_id, data)

    # 2. Asegurar Sales workflow RUNNING. No signaleamos nada — el handoff
    #    viaja por metadata, no por signal. Si Sales ya corre, no hace falta
    #    hacer nada mas: su loop principal lee metadata en cada iteracion. Si
    #    no corre, arrancamos uno nuevo (idempotente vs WorkflowAlreadyStartedError).
    client = await get_temporal_client()
    workflow_id = f"session-{session_id}"

    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            return  # ya corre, bootstrap no se reejecuta pero el refresh
                    # per-iteration vera el handoff
    except (RPCError, RuntimeError):
        pass

    try:
        await client.start_workflow(
            HubaraSalesSessionWorkflow.run,
            SalesSessionInput(
                session_id=session_id,
                runtime_workspace_path=str(get_workspace_path()),
            ),
            id=workflow_id,
            task_queue=SALES_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        # Race entre el describe y el start_workflow — alguien mas lo arranco.
        # Esta bien: ese workflow tambien leera el handoff de metadata.
        pass


async def start_remarketing_for_session(
    client,
    *,
    session_id: str,
    motivo: str,
    delay_seconds: int = 0,
) -> None:
    """Arranca un `RemarketingWorkflow` para `session_id` con `motivo`.

    Reusable desde:
      * `schedule_remarketing_workflow_activity` (worker, via Sales workflow).
      * `dashboard/handoff.py` (HTTP, cuando el humano devuelve el control y
        elige Remarketing como destino).

    Maneja el caso zombie: si ya existe un Remarketing previo RUNNING para la
    misma sesion, lo terminamos antes de arrancar el nuevo (los `start_workflow`
    son deliberados — siempre queremos reemplazar la sesion previa).

    Race: entre el `terminate` y el `start_workflow` otro caller podría arrancar
    el mismo workflow_id. Capturamos `WorkflowAlreadyStartedError` por simetría
    con `start_or_signal_sales_workflow_activity` — si ya hay uno corriendo, lo
    aceptamos como buen estado y seguimos.
    """
    from src.plugins.chats.agent.remarketing.config.env import (
        get_workspace_path as get_remarketing_workspace_path,
    )
    from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput
    import structlog

    log = structlog.get_logger()
    workflow_id = f"remarketing-{session_id}"
    delay = timedelta(seconds=delay_seconds)

    try:
        existing = client.get_workflow_handle(workflow_id)
        desc = await existing.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            try:
                await existing.terminate(
                    reason="Reemplazado por nuevo start_remarketing_for_session"
                )
            except Exception as e:
                # El workflow puede haber completado/terminado entre describe y
                # terminate (race). No nos importa: igual vamos a intentar
                # start_workflow abajo.
                log.info(
                    "start_remarketing: terminate race",
                    workflow_id=workflow_id,
                    error=str(e),
                )
    except RPCError:
        # No existe — ok, arranca limpio.
        pass

    try:
        await client.start_workflow(
            "RemarketingWorkflow",
            RemarketingSessionInput(
                session_id=session_id,
                motivo=motivo,
                runtime_workspace_path=str(get_remarketing_workspace_path()),
            ),
            id=workflow_id,
            task_queue=REMARKETING_QUEUE,
            start_delay=delay,
        )
    except WorkflowAlreadyStartedError:
        # Race: alguien arrancó un workflow con el mismo ID entre nuestro
        # terminate y el start_workflow. Aceptable — la sesión está cubierta.
        log.info(
            "start_remarketing: workflow already started (race after terminate)",
            workflow_id=workflow_id,
        )


async def terminate_session_workflows(client, session_id: str) -> list[str]:
    """Termina cualquier workflow Sales/Remarketing RUNNING para `session_id`.

    Usado por el endpoint `intervene` del dashboard handoff: si el humano toma
    el control mientras un workflow está procesando un turno, evitamos que el
    bot responda en paralelo al humano.

    **Best-effort**: capturamos CUALQUIER excepción por workflow individual
    (RPC error, race entre describe y terminate, conexión interrumpida) y
    seguimos con el siguiente. La metadata ya quedó marcada como `humano`
    antes de llamarnos; si no podemos terminar un workflow, el peor caso es
    que envíe UN último turno al cliente — molesto pero no catastrófico, y
    el operador puede pulsar Intervenir de nuevo.

    Retorna la lista de workflow_ids que efectivamente terminaron (útil para
    logging/telemetría).
    """
    import structlog

    log = structlog.get_logger()
    terminated: list[str] = []
    for workflow_id in (f"session-{session_id}", f"remarketing-{session_id}"):
        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()
            if desc.status == WorkflowExecutionStatus.RUNNING:
                await handle.terminate(reason="Humano tomó el control via dashboard")
                terminated.append(workflow_id)
        except RPCError:
            # No existe — ok, salta al siguiente.
            pass
        except Exception as e:
            # Cualquier otra falla (timeout, race entre describe y terminate,
            # cluster temporal inaccesible): logueamos y seguimos. Mejor
            # dejar al humano sin termination explícita que romper el
            # endpoint y bloquear el take-over.
            log.warning(
                "terminate_session_workflows: best-effort failure",
                workflow_id=workflow_id,
                error=str(e),
            )
    return terminated


@activity.defn(name="schedule_remarketing_workflow")
async def schedule_remarketing_workflow_activity(decision: ScheduleRemarketingDecision) -> None:
    """Programa el RemarketingWorkflow con `start_delay`.

    Reemplaza la logica que vivia dentro de `ManageConversationTagTool.execute`
    cuando el tag era INTERESADO. Delega en `start_remarketing_for_session`
    (helper reusable desde HTTP también).
    """
    client = await get_temporal_client()
    await start_remarketing_for_session(
        client,
        session_id=decision.session_id,
        motivo=decision.motivo,
        delay_seconds=decision.delay_seconds,
    )
