import asyncio
from loguru import logger
from temporalio.client import Client
from temporalio.worker import Worker

from exoclaw_temporal.session_based.workflows.agent_session import AgentSessionWorkflow
from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat
from src.core.activities import execute_tool  # Sobreescritura asíncrona de herramientas centralizada
from src.core.temporal_client import get_temporal_client

from src.domains.sales_whatsapp.service import SALES_QUEUE

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
        workflows=[AgentSessionWorkflow],
        activities=[build_prompt, llm_chat, execute_tool, record_turn],
    )
    
    logger.info("😎 Sales Agent En Vivo. Escuchando la cola exclusiva: '{}'", SALES_QUEUE)
    await worker.run()

if __name__ == "__main__":
    asyncio.run(run_worker())
