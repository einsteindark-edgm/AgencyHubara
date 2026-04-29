import asyncio
from loguru import logger
from temporalio.worker import Worker

from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat
from src.core.activities import execute_tool
from src.core.infrastructure.whatsapp.activities import send_whatsapp_message_activity
from src.core.infrastructure.temporal.dispatcher_activities import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
)
from src.domains.sales_whatsapp.activities import decide_ghosting_action
from src.core.temporal_client import get_temporal_client

from src.core.constants import SALES_QUEUE

import sys
import logging

class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists.
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

async def run_worker():
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
            decide_ghosting_action,
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
        ],
    )
    
    logger.info("😎 Sales Agent En Vivo. Escuchando la cola exclusiva: '{}'", SALES_QUEUE)
    await worker.run()

if __name__ == "__main__":
    asyncio.run(run_worker())
