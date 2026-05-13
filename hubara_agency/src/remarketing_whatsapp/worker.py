import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.temporal.activities import (
    claim_conversation_routing,
    execute_tool,
    read_workspace_memory_activity,
)
from src.platform.constants import REMARKETING_QUEUE
from src.platform.temporal.dispatcher import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
)
from src.platform.whatsapp.activities import send_whatsapp_message_activity
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension
from src.remarketing_whatsapp.activities import (
    bootstrap_remarketing_session_activity,
    build_remarketing_trigger_activity,
)
from src.platform.session_history.activities import persist_assistant_message_activity
from src.remarketing_whatsapp.workflows.remarketing import RemarketingSessionWorkflow
from src.platform.tools.routing import TransferToSalesAgentTool
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
            persist_assistant_message_activity,
            read_workspace_memory_activity,
            build_remarketing_trigger_activity,
            bootstrap_remarketing_session_activity,
            # PR-D global cleanup (ADR-2026-05-06-10): la
            # `@activity.defn load_remarketing_brain_activity` fue eliminada del
            # codigo. Las fixtures regeneradas a v3 no la referencian y el
            # workflow no la invoca. Si en produccion quedan in-flight workflows
            # con events `load_remarketing_brain_activity` en su history, drenar
            # antes de deployar (idle timeout de Remarketing es 24h).
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
        ],
    )

    logger.info("🎯 Remarketing Agent En Vivo. Escuchando la cola exclusiva: '{}'", REMARKETING_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
