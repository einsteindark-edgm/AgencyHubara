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

from src.platform.constants import SALES_QUEUE, REMARKETING_QUEUE
from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision
from src.platform.temporal.client import get_temporal_client


@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None:
    """Arranca el HubaraSalesSessionWorkflow si no corre, le manda signal con el resumen.

    Reemplaza la logica que vivia dentro de `TransferToSalesAgentTool.execute`.
    """
    # Imports locales para evitar ciclos: el workflow importa este modulo a traves
    # de `imports_passed_through`, y workflows no deben importarse mutuamente.
    from src.sales_whatsapp.config.env import get_workspace_path
    from src.sales_whatsapp.contracts import SalesSessionInput
    from src.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow

    session_id = decision.session_id
    summary = decision.summary or "El cliente volvió a interactuar"

    client = await get_temporal_client()
    workflow_id = f"session-{session_id}"

    handle = None
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            raise RuntimeError("Sales workflow not running")
    except (RPCError, RuntimeError):
        # F7: el dispatcher ya no construye SessionInput. El workflow lo arma
        # via `bootstrap_sales_session_activity` como primera activity.
        # PR-A: resolvemos el runtime workspace path del agente Sales para que
        # cruce el boundary. PR-B lo consume.
        try:
            handle = await client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id=session_id,
                    runtime_workspace_path=str(get_workspace_path()),
                ),
                id=workflow_id,
                task_queue=SALES_QUEUE,
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(workflow_id)

    await handle.signal(
        HubaraSalesSessionWorkflow.send_message,
        args=[
            f"[SISTEMA INTERNO]: Remarketing acaba de recuperar al cliente. El usuario dijo: '{summary}'. TU TURNO. SALUDA AL CLIENTE Y RETOMA LA VENTA COMO SI NADA HUBIERA PASADO.",
            None,
            None,  # plugin_context: el workflow carga su propio brain
        ],
    )


@activity.defn(name="schedule_remarketing_workflow")
async def schedule_remarketing_workflow_activity(decision: ScheduleRemarketingDecision) -> None:
    """Programa el RemarketingWorkflow con `start_delay`.

    Reemplaza la logica que vivia dentro de `ManageConversationTagTool.execute`
    cuando el tag era INTERESADO.
    """
    # PR-A remarketing: alias el getter para evitar colision con el de Sales si
    # algun futuro caller lo importa en el mismo modulo.
    from src.remarketing_whatsapp.config.env import (
        get_workspace_path as get_remarketing_workspace_path,
    )
    from src.remarketing_whatsapp.contracts import RemarketingSessionInput

    client = await get_temporal_client()
    delay = timedelta(seconds=decision.delay_seconds)
    workflow_id = f"remarketing-{decision.session_id}"

    # POST-MORTEM zombie remarketing-wa_573125671604: si quedo un workflow
    # Remarketing previo en estado RUNNING (porque su signal handover nunca
    # llego — Bug 1 de los paths divergentes de metadata.json), el `start_workflow`
    # nuevo lanza WorkflowAlreadyStartedError. El comportamiento anterior
    # (`except: pass`) silenciaba el error y NO arrancaba el remarketing nuevo,
    # dejando al cliente sin reactivacion.
    #
    # Fix: chequear si hay zombie y terminarlo antes de arrancar el nuevo.
    # Dado que el siguiente start_workflow es una decision deliberada del
    # negocio (Sales acaba de etiquetar INTERESADO), siempre queremos reemplazar
    # la sesion previa de Remarketing con una nueva.
    try:
        existing = client.get_workflow_handle(workflow_id)
        desc = await existing.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            await existing.terminate(reason="Reemplazado por nuevo schedule_remarketing")
    except RPCError:
        # No existe — ok, arranca limpio.
        pass

    await client.start_workflow(
        "RemarketingWorkflow",
        RemarketingSessionInput(
            session_id=decision.session_id,
            motivo=decision.motivo,
            runtime_workspace_path=str(get_remarketing_workspace_path()),
        ),
        id=workflow_id,
        task_queue=REMARKETING_QUEUE,
        start_delay=delay,
    )
