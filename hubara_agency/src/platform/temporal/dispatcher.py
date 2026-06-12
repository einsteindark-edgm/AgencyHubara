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
from src.platform.plugin_manifest import get_task_queue
from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision
from src.platform.state import FilesystemMetadataStore
from src.platform.temporal.client import get_temporal_client


def _append_pending_handoff(session_id: str, summary: str) -> None:
    """Append (NO overwrite) a ``pending_handoff_summary`` in metadata.

    M2/L-13 (runs 3607aecc + 8894825b): cada write pisaba al anterior — si el
    target aún no leyó, el contexto previo se PERDÍA (el "Dame 3" del cliente
    murió así). Append con ``\\n`` preserva todos los handoffs hasta la próxima
    lectura (``read_and_clear`` devuelve el blob completo y limpia). Si el
    summary exacto ya está contenido, skip — idempotencia ante retries de la
    activity.
    """
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    data = metadata_store.read(session_id)
    existing = data.get("pending_handoff_summary") or ""
    if summary in existing:
        return
    data["pending_handoff_summary"] = (
        f"{existing}\n{summary}" if existing else summary
    )
    metadata_store.write(session_id, data)


@activity.defn(name="write_pending_handoff")
async def write_pending_handoff_activity(session_id: str, summary: str) -> None:
    """Persist a handoff summary into the session metadata.

    Generic side-effect activity used by ADR-2026-05-20 (declarative
    orchestration): a sibling workflow emits a completion event; the dispatcher
    routes to the target workflow; this activity runs (typically as a separate
    explicit step from the source workflow) to seed handoff context.

    Lives in platform/ and does NOT touch any agent module — it just writes a
    string into ``metadata.pending_handoff_summary`` (POSIX-atomic via
    ``FilesystemMetadataStore``). The target workflow's bootstrap reads the
    field on cold start; warm refreshes re-read it per-iteration (see
    ``read_and_clear_pending_handoff_activity``). Append-mode (M2/L-13):
    múltiples writes antes de una lectura se acumulan, no se pisan.
    """
    _append_pending_handoff(session_id, summary)


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

    ADR-2026-05-20 (Level 3 declarative orchestration): este activity sigue
    existiendo como path legacy + self-loop dentro de sales (sales se invoca
    a si mismo desde su tool TransferToSales). El cuerpo se refactorizó:
    ya NO importa la clase ``HubaraSalesSessionWorkflow`` ni el DTO
    ``SalesSessionInput`` — usa ``get_workflow_name("chats", "sales")`` + un
    dict input. Eso elimina las ``ignore_imports`` del .importlinter para
    este módulo (R-DIP #9 + R-DIP #10 cumplen sin excepciones).

    Los nuevos call sites cross-agent (e.g. remarketing → sales) usan
    ``dispatch_event_activity`` + ``CustomerRepliedDuringRemarketingEvent``
    en lugar de invocar este activity directamente. Cuando todos los call
    sites cross-agent estén migrados, este activity quedará solo como
    self-loop de sales (sales → sales) — ahí se puede inline al workflow.
    """
    from src.platform.plugin_manifest import get_workflow_name

    session_id = decision.session_id
    summary = decision.summary or "El cliente volvió a interactuar"

    # 1. Persistir contexto de handoff en metadata. Mismo helper append-mode
    #    que ``write_pending_handoff_activity`` (M2/L-13: no pisar handoffs
    #    que el target aún no leyó; no se invoca como activity-nested porque
    #    este body YA es activity).
    _append_pending_handoff(session_id, summary)

    # 2. Asegurar Sales workflow RUNNING. No signaleamos nada — el handoff
    #    viaja por metadata, no por signal. Si Sales ya corre, no hace falta
    #    hacer nada mas: su loop principal lee metadata en cada iteracion. Si
    #    no corre, arrancamos uno nuevo (idempotente vs WorkflowAlreadyStartedError).
    #
    # ADR-2026-05-20: dispatch por string del manifest (NO importamos
    # HubaraSalesSessionWorkflow). El input también se construye como dict —
    # Temporal lo deserializa al SalesSessionInput type hint del worker target.
    # ``runtime_workspace_path`` se omite del input: el bootstrap activity
    # del worker target lo resuelve via fallback a su config local.
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

    workflow_name = get_workflow_name("chats", "sales")
    try:
        await client.start_workflow(
            workflow_name,
            {"session_id": session_id},
            id=workflow_id,
            task_queue=get_task_queue("chats", "sales"),
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
      * `schedule_remarketing_workflow_activity` (worker, via Sales workflow,
        path legacy pre-ADR-2026-05-20).
      * `dashboard/handoff.py` (HTTP, cuando el humano devuelve el control y
        elige Remarketing como destino).

    Maneja el caso zombie: si ya existe un Remarketing previo RUNNING para la
    misma sesion, lo terminamos antes de arrancar el nuevo (los `start_workflow`
    son deliberados — siempre queremos reemplazar la sesion previa).

    Race: entre el `terminate` y el `start_workflow` otro caller podría arrancar
    el mismo workflow_id. Capturamos `WorkflowAlreadyStartedError` por simetría
    con `start_or_signal_sales_workflow_activity` — si ya hay uno corriendo, lo
    aceptamos como buen estado y seguimos.

    ADR-2026-05-20: dispatch por nombre desde el manifest. NO importa
    ``RemarketingSessionWorkflow``, NO importa ``RemarketingSessionInput`` —
    pasa un dict que Temporal deserializa al type hint del worker target. El
    ``runtime_workspace_path`` se omite (el bootstrap del target hace fallback
    a su config local).
    """
    import structlog

    from src.platform.plugin_manifest import get_workflow_name

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

    workflow_name = get_workflow_name("chats", "remarketing")
    try:
        await client.start_workflow(
            workflow_name,
            {"session_id": session_id, "motivo": motivo},
            id=workflow_id,
            task_queue=get_task_queue("chats", "remarketing"),
            start_delay=delay if delay > timedelta(seconds=0) else None,
        )
    except WorkflowAlreadyStartedError:
        # Race: alguien arrancó un workflow con el mismo ID entre nuestro
        # terminate y el start_workflow. Aceptable — la sesión está cubierta.
        log.info(
            "start_remarketing: workflow already started (race after terminate)",
            workflow_id=workflow_id,
        )


async def terminate_session_workflows(
    client,
    session_id: str,
    *,
    extra_workflow_ids: Sequence[str] = (),
) -> list[str]:
    """Termina cualquier workflow Sales/Remarketing RUNNING para `session_id`.

    Usado por el endpoint `intervene` del dashboard handoff: si el humano toma
    el control mientras un workflow está procesando un turno, evitamos que el
    bot responda en paralelo al humano.

    `extra_workflow_ids`: ids adicionales a terminar junto con los canónicos
    `session-{id}` / `remarketing-{id}`. El endpoint los usa para cerrar el
    watchdog per-episodio (`watchdog-{session_id}-{episode_id}`), que NO sigue
    el patrón de prefijo de sesión y por eso no se descubre solo. Keyword-only
    para no romper los callers existentes (firma backward-compatible).

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
    workflow_ids = [
        f"session-{session_id}",
        f"remarketing-{session_id}",
        *extra_workflow_ids,
    ]
    for workflow_id in workflow_ids:
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
