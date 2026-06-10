import asyncio

from loguru import logger
from temporalio.worker import Worker


from src.platform.logging import setup_logging
from src.platform.observability import init_otel, otel_workflow_runner
from src.platform.plugin_manifest import get_task_queue
from src.platform.plugin_runtime import ensure_plugin_enabled
from src.platform.session_history.activities import (
    persist_assistant_message_activity,
)
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension
from src.platform.tools.escalation import EscalateToHumanTool
from src.platform.workflow_helpers import CONVERSATIONAL_TURN_ACTIVITIES
from src.platform.whatsapp.activities import (
    send_whatsapp_message_activity,
    send_whatsapp_template_activity,
)
from src.plugins.eta.agent.eta.activities import (
    all_trackings_terminal_activity,
    bootstrap_eta_session_activity,
    claim_eta_notification_activity,
    record_eta_notification_activity,
    start_eta_tracking_activity,
)
from src.plugins.eta.agent.eta.workflows.eta_session import (
    HubaraEtaSessionWorkflow,
)

setup_logging()

# OTel obs (HU-003): bootstrap OTel ANTES de cualquier llm_chat. No-op si
# OTEL_SDK_DISABLED=true; consola si no hay OTEL_EXPORTER_OTLP_ENDPOINT.
init_otel("eta-agent")

# Composition root del worker ETA: una sola tool LLM. El agente es un
# NOTIFICADOR PURO (convivencia ETA/Sales 2026-06-10): no recibe inbounds —
# los atiende Sales (tool `check_order_status`). Escalate queda para el caso
# excepcional de detectar una anomalía AL GENERAR una notificación.
# GOTCHA #6: la clase referenciada en la lambda DEBE estar importada al top
# (lo está — `ruff --select F821` lo verifica).
register_tool_extension(
    "eta.escalate_to_human",
    lambda workspace: EscalateToHumanTool(workspace=str(workspace)),
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
            # Set conversacional compartido — TODO worker que corra
            # run_agent_turn lo spread-ea desde workflow_helpers (fuente
            # única, L-3: este worker registraba 5 de las 6 a mano;
            # `record_episode_llm_usage` faltó y el primer cliente que
            # conversó con el agente mató el workflow con NotFoundError).
            *CONVERSATIONAL_TURN_ACTIVITIES,
            # Outbound + persistencia para el dashboard.
            send_whatsapp_message_activity,
            # Template de utilidad para notificaciones fuera de la ventana 24h.
            send_whatsapp_template_activity,
            persist_assistant_message_activity,
            # Domain-specific del agente ETA.
            bootstrap_eta_session_activity,
            start_eta_tracking_activity,
            claim_eta_notification_activity,
            record_eta_notification_activity,
            all_trackings_terminal_activity,
        ],
        workflow_runner=otel_workflow_runner(),
    )

    logger.info("📦 ETA Agent En Vivo. Escuchando la cola exclusiva: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
