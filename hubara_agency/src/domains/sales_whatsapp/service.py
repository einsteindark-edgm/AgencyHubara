import asyncio
from pathlib import Path
from temporalio.service import RPCError
import structlog

logger = structlog.get_logger()
from src.core.temporal_client import get_temporal_client
from src.core.registries import build_default_llm_config, build_workspace_config, get_base_tools_registry, get_base_tools_json
from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
from exoclaw_temporal.config import SessionInput
from src.domains.sales_whatsapp import integrations as whatsapp_client
import json
from src.core.config import WORKSPACE_VAULT_DIR
from src.domains.remarketing_whatsapp.workflows.remarketing import RemarketingSessionWorkflow, _load_remarketing_brain
SALES_QUEUE = "queue-sales-agent"

def _load_shared_brain() -> list[str]:
    """Carga los md del Cerebro Compartido dinámicamente."""
    brain_dir = Path(__file__).parent / "shared_brain"
    files = ["identity.md", "knowledge.md", "instructions.md"]
    context = []
    for f in files:
        filepath = brain_dir / f
        if filepath.exists():
            context.append(filepath.read_text(encoding="utf-8"))
    return context

async def process_incoming_message(body: dict):
    """
    Coordina la interacción entre los recursos de la red social (WhatsApp), 
    la inyección del payload hacia Temporal, y despacha a la integración cliente saliente.
    """
    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        
        if "messages" in value and value["messages"]:
            message_obj = value["messages"][0]
            phone_number_id = value["metadata"]["phone_number_id"]
            from_number = message_obj["from"]
            
            if message_obj["type"] == "text":
                message_text = message_obj["text"]["body"]
                session_id = f"wa_{from_number}"
                
                logger.info("WhatsApp Message Received", message_text=message_text, from_number=from_number)
                
                # Signal the Temporal workflow. It will handle the response asynchronously.
                await _signal_temporal_and_poll(session_id, message_text, phone_number_id)
                
    except (KeyError, IndexError) as e:
        logger.error("Error parseando el payload de Meta", error=str(e))


async def _signal_temporal_and_poll(session_id: str, message: str, phone_number_id: str | None = None) -> str | None:
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    history_file = WORKSPACE_VAULT_DIR / session_id / "sessions" / f"{session_id}.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    active_route = "ventas"
    data = {}
    
    # 1. Asegurar el log de entrada del cliente antes de ir a memoria viva (Temporal)
    with history_file.open("a", encoding="utf-8") as f:
        user_event = {"role": "user", "content": message}
        f.write(json.dumps(user_event, ensure_ascii=False) + "\n")
    
    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            active_route = data.get("active_route", "ventas")
        except json.JSONDecodeError:
            pass
            
    if phone_number_id:
        data["phone_number_id"] = phone_number_id
        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    client = await get_temporal_client()
    
    # Intentamos la ruta de remarketing si está activa
    if active_route == "remarketing":
        workflow_id = f"remarketing-{session_id}"
        logger.info("Routing webhook to Remarketing Agent", workflow_id=workflow_id)
        workflow_class = RemarketingSessionWorkflow
        plugin_context = _load_remarketing_brain()
        
        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()
            from temporalio.client import WorkflowExecutionStatus
            if desc.status != WorkflowExecutionStatus.RUNNING:
                raise RuntimeError("Remarketing workflow is no longer running")
        except Exception:
            # Si se murió el workflow de remarketing, hacemos downgrade a ventas
            logger.warning("Remarketing workflow not found or finished, falling back to Sales")
            active_route = "ventas"
            
    if active_route != "remarketing":
        workflow_id = f"session-{session_id}"
        logger.info("Routing webhook to Sales Agent", workflow_id=workflow_id)
        workflow_class = HubaraSalesSessionWorkflow
        plugin_context = _load_shared_brain()
        
        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()  
            from temporalio.client import WorkflowExecutionStatus
            if desc.status != WorkflowExecutionStatus.RUNNING:
                raise RuntimeError("Workflow is no longer running")
        except Exception:
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
    
    # We return immediately. The actual WhatsApp send occurs asynchronously from within the workflow loop.
    return None
