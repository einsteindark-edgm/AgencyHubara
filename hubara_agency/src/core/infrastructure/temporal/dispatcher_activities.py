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
from pathlib import Path

from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from exoclaw_temporal.config import SessionInput

from src.core.constants import SALES_QUEUE, REMARKETING_QUEUE
from src.core.contracts import ScheduleRemarketingDecision, TransferDecision
from src.core.registries import (
    build_default_llm_config,
    build_workspace_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.core.temporal_client import get_temporal_client


@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None:
    """Arranca el HubaraSalesSessionWorkflow si no corre, le manda signal con el resumen.

    Reemplaza la logica que vivia dentro de `TransferToSalesAgentTool.execute`.
    """
    # Imports locales para evitar ciclos: el workflow importa este modulo a traves
    # de `imports_passed_through`, y workflows no deben importarse mutuamente.
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
        llm = build_default_llm_config()
        ws = build_workspace_config(session_id)
        registry = get_base_tools_registry(Path(ws.path))

        try:
            handle = await client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SessionInput(
                    session_id=session_id,
                    channel="whatsapp",
                    chat_id=session_id,
                    llm=llm,
                    workspace=ws,
                    tool_definitions_json=get_base_tools_json(registry),
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
