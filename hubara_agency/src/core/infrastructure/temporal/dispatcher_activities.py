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

from src.core.constants import SALES_QUEUE, REMARKETING_QUEUE
from src.core.contracts import ScheduleRemarketingDecision, TransferDecision
from src.core.temporal_client import get_temporal_client


@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None:
    """Arranca el HubaraSalesSessionWorkflow si no corre, le manda signal con el resumen.

    Reemplaza la logica que vivia dentro de `TransferToSalesAgentTool.execute`.
    """
    # Imports locales para evitar ciclos: el workflow importa este modulo a traves
    # de `imports_passed_through`, y workflows no deben importarse mutuamente.
    from src.domains.sales_whatsapp.contracts import SalesSessionInput
    from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow

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
        try:
            handle = await client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id=session_id),
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
    from src.domains.remarketing_whatsapp.contracts import RemarketingSessionInput

    client = await get_temporal_client()
    delay = timedelta(seconds=decision.delay_seconds)

    try:
        await client.start_workflow(
            "RemarketingWorkflow",
            RemarketingSessionInput(session_id=decision.session_id, motivo=decision.motivo),
            id=f"remarketing-{decision.session_id}",
            task_queue=REMARKETING_QUEUE,
            start_delay=delay,
        )
    except WorkflowAlreadyStartedError:
        pass
