import asyncio

from exoclaw_temporal.activities.conversation import build_prompt, record_turn
from exoclaw_temporal.activities.llm import llm_chat
from loguru import logger
from temporalio.worker import Worker

from src.platform.analytics.composition import setup_analytics
from src.platform.catalog.composition import get_catalog_client
from src.platform.catalog.medusa_checkout import MedusaCheckoutVerification
from src.platform.logging import setup_logging
from src.platform.medusa.composition import get_medusa_product_service
from src.platform.orchestration import dispatch_event_activity
from src.platform.plugin_manifest import get_task_queue
from src.platform.session_history.activities import (
    persist_assistant_message_activity,
)
from src.platform.temporal.activities import execute_tool
from src.platform.temporal.client import get_temporal_client
from src.platform.temporal.dispatcher import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
    write_pending_handoff_activity,
)
from src.platform.tool_extensions import register_tool_extension
from src.platform.tools.escalation import EscalateToHumanTool
from src.platform.tools.routing import TransferToSalesAgentTool
from src.platform.whatsapp.activities import (
    send_typing_indicator_activity,
    send_whatsapp_message_activity,
)
from src.plugins.chats.agent.sales.activities import (
    bootstrap_sales_session_activity,
    decide_ghosting_action,
    flush_pending_ui_intents_activity,
    read_and_clear_pending_handoff_activity,
    transcribe_audio_activity,
)
from src.plugins.chats.agent.sales.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)
from src.plugins.chats.agent.sales.tools.checkout import VerifyOrderForCheckoutTool
from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool
from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentOrderConfirmationTool,
    PresentProductDetailTool,
    PresentProductGalleryTool,
    PresentProductsTool,
    PresentVariantPickerTool,
    ReactToMessageTool,
    RequestLocationTool,
    RequestShippingDetailsTool,
    SendContactCardTool,
    SendCTAUrlTool,
    SendQuickRepliesTool,
)

from src.plugins.chats.agent.sales.workflows.sales_session import (
    HubaraSalesSessionWorkflow,
)

setup_logging()

# HU-002: analytics bus singleton — filesystem siempre, Meta CAPI si hay
# token. El bus es global para todo el proceso del worker; las activities
# (flush_pending_ui_intents_activity, transcribe_audio_activity) leen
# desde `get_event_bus()` por su cuenta.
setup_analytics()

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

# Escalation a humano: tool inerte, escribe metadata.json[tag=HUMANO,
# active_route=humano] y devuelve un envelope con `escalation_decision`. El
# workflow lo lee, manda el mensaje de despedida del LLM y cierra. Subsecuentes
# webhooks NO arrancan workflow (`LoadOrStartSalesSession` corta cuando
# active_route==humano). Frontend ya soporta `tag=HUMANO`
# (`frontend_dashboard/src/entities/chat/model.ts:10`).
register_tool_extension(
    "sales.escalate_to_human",
    lambda workspace: EscalateToHumanTool(workspace=str(workspace)),
)

# Verificacion LIVE de precio/stock al checkout: el snapshot es la verdad
# durante la conversacion, pero al cobrar hay que confirmar contra Medusa
# por si hubo cambios reales. El verifier captura `_catalog` (snapshot) y
# `MedusaProductService` (live) por closure — singletons via lru_cache(1).
_checkout_verifier = MedusaCheckoutVerification(
    medusa=get_medusa_product_service(),
    snapshot=_catalog,
)
register_tool_extension(
    "sales.verify_order_for_checkout",
    lambda workspace: VerifyOrderForCheckoutTool(
        workspace=str(workspace),
        verifier=_checkout_verifier,
    ),
)

# HU-002: decision tools de UI rica. Emiten "intents" a
# `metadata.json[pending_ui_intents]` que `flush_pending_ui_intents_activity`
# renderiza al cliente como mensaje WA nativo (imagen, botones, lista,
# Flow, reacción, etc.) DESPUÉS del texto del LLM. Patrón documentado en
# `workspace/TOOLS.md` sección "UI Tools — Decision tools (HU-002)".
register_tool_extension(
    "sales.present_product_detail",
    lambda workspace: PresentProductDetailTool(
        workspace=str(workspace), catalog=_catalog
    ),
)
register_tool_extension(
    "sales.present_products",
    lambda workspace: PresentProductsTool(
        workspace=str(workspace), catalog=_catalog
    ),
)
register_tool_extension(
    "sales.request_location",
    lambda workspace: RequestLocationTool(workspace=str(workspace)),
)
register_tool_extension(
    "sales.request_shipping_details",
    lambda workspace: RequestShippingDetailsTool(workspace=str(workspace)),
)
register_tool_extension(
    "sales.present_order_confirmation",
    lambda workspace: PresentOrderConfirmationTool(
        workspace=str(workspace), catalog=_catalog
    ),
)
register_tool_extension(
    "sales.react_to_message",
    lambda workspace: ReactToMessageTool(workspace=str(workspace)),
)
register_tool_extension(
    "sales.send_contact_card",
    lambda workspace: SendContactCardTool(workspace=str(workspace)),
)
register_tool_extension(
    "sales.send_cta_url",
    lambda workspace: SendCTAUrlTool(workspace=str(workspace)),
)
# HU-002 / fix sesión a56bfaa9: cuando el cliente pide "más fotos", el LLM
# debe usar esta tool en vez de send_cta_url (anti-patrón que sacaba al
# cliente fuera de WhatsApp). Manda hasta 4 imágenes adicionales del MISMO
# producto como secuencia natural.
register_tool_extension(
    "sales.present_product_gallery",
    lambda workspace: PresentProductGalleryTool(
        workspace=str(workspace), catalog=_catalog
    ),
)
# HU-002 / fix sesión a56bfaa9: botones genéricos. Usados en el saludo
# inicial (3 opciones tappables) y decisiones simples mid-conversation.
register_tool_extension(
    "sales.send_quick_replies",
    lambda workspace: SendQuickRepliesTool(workspace=str(workspace)),
)
# Fix sesión 71f479f7: cuando hay ≥4 aromas/colores, lista tappable con
# emoji curado (variant_emoji.py) — más premium que listarlos en texto
# con el mismo 🌿 repetido. El emoji jamás lo elige el LLM (closed-list).
register_tool_extension(
    "sales.present_variant_picker",
    lambda workspace: PresentVariantPickerTool(workspace=str(workspace)),
)


async def main() -> None:
    """Worker Exclusivo para el Dominio de Ventas de WhatsApp."""
    logger.info("Conectando Especialista (Ventas) al clúster Temporal mTLS...")
    client = await get_temporal_client()

    task_queue = get_task_queue("chats", "sales")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HubaraSalesSessionWorkflow],
        activities=[
            build_prompt,
            llm_chat,
            execute_tool,
            record_turn,
            send_whatsapp_message_activity,
            send_typing_indicator_activity,
            persist_assistant_message_activity,
            decide_ghosting_action,
            bootstrap_sales_session_activity,
            read_and_clear_pending_handoff_activity,
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
            # ADR-2026-05-20: declarative orchestration activities.
            write_pending_handoff_activity,
            dispatch_event_activity,
            # HU-002: render UI intents emitidos por decision tools (post-LLM).
            flush_pending_ui_intents_activity,
            # HU-002 / A.5: transcripción de audio inbound (Groq/OpenAI).
            transcribe_audio_activity,
        ],
    )

    logger.info("😎 Sales Agent En Vivo. Escuchando la cola exclusiva: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
