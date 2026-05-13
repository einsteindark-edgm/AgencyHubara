import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.temporal.activities import execute_tool
from src.platform.constants import SALES_QUEUE
from src.platform.temporal.dispatcher import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
)
from src.platform.whatsapp.activities import send_whatsapp_message_activity
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension
from src.platform.session_history.activities import (
    persist_assistant_message_activity,
)
from src.sales_whatsapp.activities import (
    bootstrap_sales_session_activity,
    decide_ghosting_action,
)
from src.platform.catalog.composition import get_catalog_client
from src.sales_whatsapp.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)
from src.platform.tools.routing import TransferToSalesAgentTool
from src.sales_whatsapp.tools.tags import ManageConversationTagTool
from src.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat

setup_logging()

# NEW-5 cerrado: el composition root del worker de Sales registra las tools
# especificas del dominio. `execute_tool` las consume via
# `apply_tool_extensions`. PR-C movio `ManageConversationTagTool` aqui desde
# `core/registries.py` (DIP fix: core no debe conocer tools de dominios).
register_tool_extension(
    "sales.transfer_to_sales_agent",
    lambda workspace: TransferToSalesAgentTool(workspace=str(workspace)),
)
register_tool_extension(
    "sales.manage_conversation_tag",
    lambda workspace: ManageConversationTagTool(workspace=str(workspace)),
)

# HU-04: tools de catalogo. Leen del snapshot mantenido por catalog_sync (HU-03)
# via CatalogPort. El cliente es singleton via lru_cache(1) — capturado por
# closure en la lambda de la factory.
_catalog = get_catalog_client()

register_tool_extension(
    "sales.search_products",
    lambda workspace: SearchProductsTool(workspace=str(workspace), catalog=_catalog),
)
register_tool_extension(
    "sales.get_product_by_handle",
    lambda workspace: GetProductByHandleTool(workspace=str(workspace), catalog=_catalog),
)


async def main() -> None:
    """Worker Exclusivo para el Dominio de Ventas de WhatsApp."""
    logger.info("Conectando Especialista (Ventas) al clúster Temporal mTLS...")
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=SALES_QUEUE,
        workflows=[HubaraSalesSessionWorkflow],
        activities=[
            build_prompt,
            llm_chat,
            execute_tool,
            record_turn,
            send_whatsapp_message_activity,
            persist_assistant_message_activity,
            decide_ghosting_action,
            bootstrap_sales_session_activity,
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
        ],
    )

    logger.info("😎 Sales Agent En Vivo. Escuchando la cola exclusiva: '{}'", SALES_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
