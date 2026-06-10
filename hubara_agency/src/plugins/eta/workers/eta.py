import asyncio

from loguru import logger
from temporalio.worker import Worker

from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat

from src.platform.logging import setup_logging
from src.platform.observability import init_otel, otel_workflow_runner
from src.platform.observability.cost_attribution import (
    get_active_episode_id_activity,
)
from src.platform.plugin_manifest import get_task_queue
from src.platform.plugin_runtime import ensure_plugin_enabled
from src.platform.session_history.activities import (
    persist_assistant_message_activity,
)
from src.platform.temporal.activities import execute_tool
from src.platform.temporal.client import get_temporal_client
from src.platform.temporal.dispatcher import (
    start_or_signal_sales_workflow_activity,
)
from src.platform.tool_extensions import register_tool_extension
from src.platform.tools.escalation import EscalateToHumanTool
from src.platform.tools.routing import TransferToSalesAgentTool
from src.platform.whatsapp.activities import (
    send_typing_indicator_activity,
    send_whatsapp_message_activity,
    send_whatsapp_template_activity,
)
from src.plugins.eta.agent.eta.activities import (
    bootstrap_eta_session_activity,
    claim_eta_notification_activity,
    record_eta_notification_activity,
    record_eta_reply_activity,
    start_eta_tracking_activity,
)
from src.plugins.eta.agent.eta.workflows.eta_session import (
    HubaraEtaSessionWorkflow,
)

setup_logging()

# OTel obs (HU-003): bootstrap OTel ANTES de cualquier llm_chat. No-op si
# OTEL_SDK_DISABLED=true; consola si no hay OTEL_EXPORTER_OTLP_ENDPOINT.
init_otel("eta-agent")

# Composition root del worker ETA: registra sus dos únicas tools LLM. El agente
# ETA solo notifica; sus salidas conversacionales son (a) escalar a humano
# cuando la duda se sale de su rol, (b) transferir a Ventas si el cliente quiere
# comprar. GOTCHA #6: las clases referenciadas en las lambdas DEBEN estar
# importadas al top (lo están — `ruff --select F821` lo verifica).
register_tool_extension(
    "eta.escalate_to_human",
    lambda workspace: EscalateToHumanTool(workspace=str(workspace)),
)
register_tool_extension(
    "eta.transfer_to_sales_agent",
    lambda workspace: TransferToSalesAgentTool(workspace=str(workspace)),
)


async def main() -> None:
    """Worker exclusivo del Agente ETA (notificaciones de estado de pedido)."""
    ensure_plugin_enabled("eta")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando Especialista (ETA) al clúster Temporal mTLS...")
    client = await get_temporal_client()

    task_queue = get_task_queue("eta", "eta")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HubaraEtaSessionWorkflow],
        activities=[
            # Core conversación / LLM / tools (exoclaw + platform).
            build_prompt,
            llm_chat,
            execute_tool,
            record_turn,
            # run_agent_turn (greenfield → patched()=True) resuelve el episode_id
            # activo para la atribución de costos del LLM.
            get_active_episode_id_activity,
            # Outbound + persistencia para el dashboard.
            send_whatsapp_message_activity,
            # Template de utilidad para notificaciones fuera de la ventana 24h.
            send_whatsapp_template_activity,
            send_typing_indicator_activity,
            persist_assistant_message_activity,
            # Transferencia a Ventas (cliente quiere comprar más).
            start_or_signal_sales_workflow_activity,
            # Domain-specific del agente ETA.
            bootstrap_eta_session_activity,
            start_eta_tracking_activity,
            claim_eta_notification_activity,
            record_eta_notification_activity,
            record_eta_reply_activity,
        ],
        workflow_runner=otel_workflow_runner(),
    )

    logger.info("📦 ETA Agent En Vivo. Escuchando la cola exclusiva: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
