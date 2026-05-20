import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.temporal.activities import (
    claim_conversation_routing,
    execute_tool,
    read_workspace_memory_activity,
)
from src.platform.orchestration import dispatch_event_activity
from src.platform.plugin_manifest import get_task_queue
from src.platform.temporal.dispatcher import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
    write_pending_handoff_activity,
)
from src.platform.whatsapp.activities import (
    send_typing_indicator_activity,
    send_whatsapp_message_activity,
)
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension
from src.plugins.chats.agent.remarketing.activities import (
    bootstrap_remarketing_session_activity,
    build_remarketing_trigger_activity,
)
from src.platform.session_history.activities import persist_assistant_message_activity
from src.plugins.chats.agent.remarketing.workflows.remarketing import RemarketingSessionWorkflow
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

    task_queue = get_task_queue("chats", "remarketing")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[RemarketingSessionWorkflow],
        activities=[
            build_prompt,
            llm_chat,
            execute_tool,
            record_turn,
            claim_conversation_routing,
            send_whatsapp_message_activity,
            send_typing_indicator_activity,
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
            # ADR-2026-05-20: declarative orchestration activities.
            write_pending_handoff_activity,
            dispatch_event_activity,
        ],
    )

    logger.info("🎯 Remarketing Agent En Vivo. Escuchando la cola exclusiva: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
