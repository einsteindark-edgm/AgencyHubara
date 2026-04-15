import asyncio
from pathlib import Path
from temporalio.service import RPCError

from src.core.temporal_client import get_temporal_client
from src.core.registries import build_default_llm_config, build_workspace_config, get_base_tools_registry, get_base_tools_json
from exoclaw_temporal.session_based.workflows.agent_session import AgentSessionWorkflow
from exoclaw_temporal.config import SessionInput
from src.domains.sales_whatsapp import integrations as whatsapp_client

SALES_QUEUE = "queue-sales-agent"

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
                
                print(f"📥 WhatsApp: {message_text} de {from_number}")
                
                # Delegamos a temporal y esperamos el result asíncronamente
                response_text = await _signal_temporal_and_poll(session_id, message_text)
                
                # Llamada agnóstica al Infra Cliente
                if response_text:
                    await whatsapp_client.send_message(phone_number_id, from_number, response_text)
                
    except (KeyError, IndexError) as e:
        print(f"Error parseando el payload de Meta: {e}")


async def _signal_temporal_and_poll(session_id: str, message: str) -> str | None:
    client = await get_temporal_client()
    workflow_id = f"session-{session_id}"
    handle = None
    
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.describe()  
    except RPCError:
        print(f"🌟 Creando AgentSessionWorkflow para {workflow_id}")
        llm = build_default_llm_config()
        ws = build_workspace_config(session_id)
        registry = get_base_tools_registry(Path(ws.path))
        
        handle = await client.start_workflow(
            AgentSessionWorkflow.run,
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

    await handle.signal(AgentSessionWorkflow.send_message, message) # type: ignore[attr-defined]

    while True:
        is_proc = await handle.query(AgentSessionWorkflow.is_processing) # type: ignore[attr-defined]
        if not is_proc:
            break
        await asyncio.sleep(0.5)

    return await handle.query(AgentSessionWorkflow.get_last_response) # type: ignore[attr-defined]
