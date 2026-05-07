import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.core.activities import (
    claim_conversation_routing,
    execute_tool,
    read_workspace_memory_activity,
)
from src.core.constants import REMARKETING_QUEUE
from src.core.infrastructure.temporal.dispatcher_activities import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
)
from src.core.infrastructure.whatsapp.activities import send_whatsapp_message_activity
from src.core.logging import setup_logging
from src.core.temporal_client import get_temporal_client
from src.core.tool_extensions import register_tool_extension
from src.domains.remarketing_whatsapp.activities import (
    bootstrap_remarketing_session_activity,
    build_remarketing_trigger_activity,
    load_remarketing_brain_activity,
)
from src.domains.remarketing_whatsapp.workflows.remarketing import RemarketingSessionWorkflow
from src.domains.sales_whatsapp.tools.routing import TransferToSalesAgentTool
from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat

setup_logging()

# NEW-5 cerrado: el worker de Remarketing tambien necesita la tool de
# transferencia (es la unica forma de que el agente vuelva a Ventas).
# Composition root legitimo: el worker conoce ambos lados (core + domain).
register_tool_extension(
    "sales.transfer_to_sales_agent",
    lambda workspace: TransferToSalesAgentTool(workspace=str(workspace)),
)


async def main() -> None:
    """Worker Exclusivo para el Dominio de Remarketing."""
    logger.info("Conectando Especialista (Remarketing) al clúster Temporal mTLS...")
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=REMARKETING_QUEUE,
        workflows=[RemarketingSessionWorkflow],
        activities=[
            build_prompt,
            llm_chat,
            execute_tool,
            record_turn,
            claim_conversation_routing,
            send_whatsapp_message_activity,
            read_workspace_memory_activity,
            build_remarketing_trigger_activity,
            bootstrap_remarketing_session_activity,
            load_remarketing_brain_activity,
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
        ],
    )

    logger.info("🎯 Remarketing Agent En Vivo. Escuchando la cola exclusiva: '{}'", REMARKETING_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
