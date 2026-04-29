import json
from pathlib import Path

import structlog
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError

from exoclaw_temporal.config import SessionInput

from src.core.config import WORKSPACE_VAULT_DIR
from src.core.constants import ROUTE_REMARKETING, ROUTE_VENTAS, SALES_QUEUE, WHATSAPP_SESSION_PREFIX
from src.core.registries import build_default_llm_config, build_workspace_config, get_base_tools_registry, get_base_tools_json
from src.core.temporal_client import get_temporal_client
from src.domains.sales_whatsapp.parsers import WhatsAppMessage, parse_whatsapp_inbound
from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
from src.domains.remarketing_whatsapp.workflows.remarketing import RemarketingSessionWorkflow, _load_remarketing_brain

logger = structlog.get_logger()

_SALES_BRAIN_DIR = Path(__file__).parent / "shared_brain"


def _load_shared_brain() -> list[str]:
    from src.core.brains import load_brain
    return load_brain(_SALES_BRAIN_DIR)

async def process_incoming_message(parsed: WhatsAppMessage) -> None:
    """Despacha un mensaje ya parseado al workflow Temporal correspondiente.

    El parser se aplica en el handler HTTP para distinguir errores 4xx (body
    malformed) de errores 5xx (workflow falla). Esta funcion asume input ya valido.
    """
    if parsed.text is None:
        logger.info("Non-text WhatsApp message ignored", message_type=(parsed.media or {}).get("type"))
        return

    session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"
    logger.info("WhatsApp Message Received", message_text=parsed.text, from_number=parsed.from_number)
    await _signal_temporal_and_poll(session_id, parsed.text, parsed.phone_number_id)


async def _signal_temporal_and_poll(session_id: str, message: str, phone_number_id: str | None = None) -> str | None:
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    history_file = WORKSPACE_VAULT_DIR / session_id / "sessions" / f"{session_id}.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    active_route = ROUTE_VENTAS
    data = {}

    # 1. Asegurar el log de entrada del cliente antes de ir a memoria viva (Temporal)
    with history_file.open("a", encoding="utf-8") as f:
        user_event = {"role": "user", "content": message}
        f.write(json.dumps(user_event, ensure_ascii=False) + "\n")

    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            active_route = data.get("active_route", ROUTE_VENTAS)
        except json.JSONDecodeError:
            pass
            
    if phone_number_id:
        data["phone_number_id"] = phone_number_id
        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    client = await get_temporal_client()
    
    # Intentamos la ruta de remarketing si está activa
    if active_route == ROUTE_REMARKETING:
        workflow_id = f"remarketing-{session_id}"
        logger.info("Routing webhook to Remarketing Agent", workflow_id=workflow_id)
        workflow_class = RemarketingSessionWorkflow
        plugin_context = _load_remarketing_brain()

        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()
            if desc.status != WorkflowExecutionStatus.RUNNING:
                raise RuntimeError("Remarketing workflow is no longer running")
        except (RPCError, RuntimeError):
            # Si se murió el workflow de remarketing, hacemos downgrade a ventas
            logger.warning("Remarketing workflow not found or finished, falling back to Sales")
            active_route = ROUTE_VENTAS

    if active_route != ROUTE_REMARKETING:
        workflow_id = f"session-{session_id}"
        logger.info("Routing webhook to Sales Agent", workflow_id=workflow_id)
        workflow_class = HubaraSalesSessionWorkflow
        plugin_context = _load_shared_brain()
        
        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()
            if desc.status != WorkflowExecutionStatus.RUNNING:
                raise RuntimeError("Workflow is no longer running")
        except (RPCError, RuntimeError):
            logger.info("Creando HubaraSalesSessionWorkflow", workflow_id=workflow_id)
            llm = build_default_llm_config()
            ws = build_workspace_config(session_id)
            registry = get_base_tools_registry(Path(ws.path))
            
            handle = await client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SessionInput(
                    session_id=session_id,
                    channel="whatsapp",
                    chat_id=session_id,
                    llm=llm,
                    workspace=ws,
                    tool_definitions_json=get_base_tools_json(registry)
                ),
                id=workflow_id,
                task_queue=SALES_QUEUE,
            )

    await handle.signal(
        workflow_class.send_message, # type: ignore[attr-defined]
        args=[message, None, plugin_context]
    )
    return None
