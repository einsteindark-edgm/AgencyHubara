# ruff: noqa: E402
# (Los imports van DESPUÉS de init_otel() a propósito — Bug A HU-003: hay que
#  parchear litellm con OpenLIT antes de que el provider importe `acompletion`,
#  sino el usage del LLM no se captura. Ver la nota detallada abajo.)
import asyncio

# Bug A (orden de imports — HU-003): init_otel() DEBE correr ANTES de importar el
# provider litellm. El provider hace `from litellm import acompletion` al
# importarse; si OpenLIT no parcheó litellm todavía, esa referencia queda sin
# instrumentar → el span gen_ai se crea pero gen_ai.usage.* (tokens/cost) sale 0.
# Por eso setup_logging + init_otel van acá arriba, antes de exoclaw_temporal.*
from src.platform.logging import setup_logging
from src.platform.observability import init_otel, otel_workflow_runner

setup_logging()
init_otel("sales-agent")

from loguru import logger
from temporalio.worker import Worker

from src.platform.analytics.composition import setup_analytics
from src.platform.catalog.composition import get_catalog_client
from src.platform.catalog.medusa_checkout import MedusaCheckoutVerification
from src.platform.medusa.composition import get_medusa_product_service
from src.platform.orchestration import dispatch_event_activity
from src.platform.orders.composition import (
    get_order_query_port,
    get_order_registration_port,
)
from src.platform.plugin_manifest import get_task_queue
from src.platform.plugin_runtime import ensure_plugin_enabled
from src.platform.session_history.activities import (
    persist_assistant_message_activity,
)
from src.platform.temporal.client import get_temporal_client
from src.platform.temporal.dispatcher import (
    schedule_remarketing_workflow_activity,
    start_or_signal_sales_workflow_activity,
    write_pending_handoff_activity,
)

from exoclaw_temporal.activities.conversation import (
    build_prompt as generic_build_prompt,
)

from src.platform.tool_extensions import register_tool_extension
from src.platform.tools.escalation import EscalateToHumanTool
from src.platform.workflow_helpers import CONVERSATIONAL_TURN_ACTIVITIES
from src.plugins.chats.agent.sales.activities.build_prompt_stage import (
    sales_build_prompt,
)
from src.platform.whatsapp.activities import (
    send_typing_indicator_activity,
    send_whatsapp_message_activity,
)
from src.platform.whatsapp.capi_activity import send_capi_event_activity
from src.plugins.chats.agent.sales.activities import (
    bootstrap_sales_session_activity,
    compute_bogota_context_activity,
    decide_ghosting_action,
    ensure_closing_escalation_activity,
    ensure_payment_pending_closure_activity,
    flush_pending_ui_intents_activity,
    read_and_clear_pending_handoff_activity,
    read_idle_timeout_seconds_activity,
    read_order_draft_note_activity,
    transcribe_audio_activity,
)
from src.plugins.chats.agent.sales.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)
from src.plugins.chats.agent.sales.tools.checkout import VerifyOrderForCheckoutTool
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.agent.sales.tools.order_status import CheckOrderStatusTool
from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)
from src.plugins.chats.agent.sales.tools.skills import LoadSkillTool
from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool
from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentOrderConfirmationTool,
    PresentProductDetailTool,
    PresentProductGalleryTool,
    PresentProductsTool,
    PresentVariantPickerTool,
    ReactToMessageTool,
    # RequestLocationTool removido (sesión adc6400c) — el botón nativo de
    # "Compartir ubicación" no funcionó en pruebas reales (cliente abandona).
    # Ahora la dirección se recolecta conversacionalmente.
    RequestShippingDetailsTool,
    SendContactCardTool,
    SendCTAUrlTool,
    SendQuickRepliesTool,
)

from src.plugins.chats.agent.sales.workflows.sales_session import (
    HubaraSalesSessionWorkflow,
)

# HU-002: analytics bus singleton — filesystem siempre, Meta CAPI si hay
# token. El bus es global para todo el proceso del worker; las activities
# (flush_pending_ui_intents_activity, transcribe_audio_activity) leen
# desde `get_event_bus()` por su cuenta.
setup_analytics()

# NEW-5 cerrado: el composition root del worker de Sales registra las tools
# especificas del dominio. `execute_tool` las consume via
# `apply_tool_extensions`. PR-C movio `ManageConversationTagTool` aqui desde
# `core/registries.py` (DIP fix: core no debe conocer tools de dominios).
#
# L-12 (run 3607aecc): `TransferToSalesAgentTool` NO se registra aqui. Es una
# tool exclusiva de Remarketing (transfiere DE remarketing HACIA ventas; ver su
# docstring). Registrada en Sales, el LLM de ventas se "autotransfería" al
# recibir el handoff de remarketing: gastaba el turno sin responder al cliente,
# pisaba `pending_handoff_summary` ajeno (perdió el "Dame 3" del cliente) y
# enviaba la jerga interna "El control ha sido transferido al agente de
# ventas." como burbuja real.
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

# Convivencia ETA/Sales (2026-06-10): el agente ETA es notificador puro — las
# preguntas de entrega ("¿cuándo llega mi pedido?") las responde SALES con
# esta tool (lee el estado compartido `metadata.eta_tracking` de la sesión).
# 2026-07-17 (HU scheduler post-venta): híbrida — además de eta_tracking lista
# los pedidos registrados de la sesión y, con el query port de orders, trae
# etapa + pay_status EN VIVO (con timeout corto; degrada a local, L-2).
# OJO gotcha #6: `get_order_query_port` capturado ANTES de la lambda — un
# símbolo solo-en-lambda carga limpio y explota en runtime de la activity.
_order_query_port = get_order_query_port()
register_tool_extension(
    "sales.check_order_status",
    lambda workspace: CheckOrderStatusTool(
        workspace=str(workspace),
        query_port=_order_query_port,
    ),
)

# Caso 573229041190 (2026-07-07): TOOLS.md instruye `load_skill("hubara_catalog")`
# para políticas estables (envíos/garantía/contra entrega/descuentos) pero la
# tool nunca estuvo registrada — el LLM quemaba una iteración con "Tool not
# found" y las políticas eran inalcanzables (terminó inventando el costo de
# envío en una orden real). Closed-list sobre workspace/skills/<name>/SKILL.md.
register_tool_extension(
    "sales.load_skill",
    lambda workspace: LoadSkillTool(workspace=str(workspace)),
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

# Sesión c4e3416f → upgrade vivo: cierre formal de la venta contra Medusa
# Draft Orders (POST /admin/draft-orders). El port se selecciona en
# composition time:
#   - MedusaOrderRegistration: si MEDUSA_REGION_ID + MEDUSA_SALES_CHANNEL_ID
#     están seteados → registra un draft order real en Medusa.
#   - StubOrderRegistration: si falta config → solo persiste a metadata local
#     y loguea WARNING (dev mode, no rompe el flujo).
# El port es singleton via lru_cache(1) capturado por closure.
_order_registration_port = get_order_registration_port()
register_tool_extension(
    "sales.register_order",
    lambda workspace: RegisterOrderTool(
        workspace=str(workspace),
        port=_order_registration_port,
    ),
)

# Breadcrumb determinista de datos del pedido: fija color/aroma/cantidad/
# ciudad/etc. en `episodes[-1].order_draft` apenas el cliente los confirma. El
# ingest los re-inyecta al prompt cada turno (via `get_projectable_draft` +
# `build_order_draft_note`) para que el LLM no re-pregunte datos ya dados.
# Tool de borde (escribe metadata.json atomico), advisory: `register_order`
# sigue siendo la fuente de verdad de la orden. `catalog`: valida aroma/color
# contra la lista cerrada del producto (caso ep_010: "Melocotón" no existe y
# entró al draft → a la orden real).
register_tool_extension(
    "sales.set_order_slot",
    lambda workspace: SetOrderSlotTool(workspace=str(workspace), catalog=_catalog),
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
# sales.request_location removido (sesión adc6400c) — el botón nativo de
# "Compartir ubicación" abre el mapa, no el formulario, y los clientes
# abandonan. La dirección se recolecta ahora vía `request_shipping_details`
# (mensaje de texto formateado) + recolección conversacional turn-by-turn.
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
# `catalog` (caso ep_010, run fa1eb974): valida las opciones contra los
# aromas/colores REALES del producto y descarta las inventadas
# ("Crema"/"Melocotón") antes de que el cliente las vea.
register_tool_extension(
    "sales.present_variant_picker",
    lambda workspace: PresentVariantPickerTool(
        workspace=str(workspace), catalog=_catalog
    ),
)


async def main() -> None:
    """Worker Exclusivo para el Dominio de Ventas de WhatsApp."""
    ensure_plugin_enabled("chats")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando Especialista (Ventas) al clúster Temporal mTLS...")
    client = await get_temporal_client()

    task_queue = get_task_queue("chats", "sales")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HubaraSalesSessionWorkflow],
        activities=[
            # Set conversacional compartido — TODO worker que corra
            # run_agent_turn lo spread-ea desde workflow_helpers (fuente
            # única, L-3). Nunca listar esas activities a mano.
            # EXCEPCIÓN Sales (dieta de prompt): `build_prompt` se reemplaza
            # por el override por-etapa `sales_build_prompt` — MISMO nombre
            # de activity, mismo contrato → el workflow no cambia (cero
            # replay implications). Registrar ambas rompería el Worker
            # (nombre duplicado), por eso se filtra la genérica.
            *(
                a
                for a in CONVERSATIONAL_TURN_ACTIVITIES
                if a is not generic_build_prompt
            ),
            sales_build_prompt,
            send_whatsapp_message_activity,
            send_typing_indicator_activity,
            persist_assistant_message_activity,
            decide_ghosting_action,
            bootstrap_sales_session_activity,
            read_and_clear_pending_handoff_activity,
            # Draft del pedido al turno de handoff (run 019f6db3): sin esto
            # el LLM arranca ciego y re-pregunta/pisa lo ya elegido.
            read_order_draft_note_activity,
            # Dynamic ghosting timeout (sesión c4e3416f): extiende el wait
            # cuando hay un WhatsApp Flow pendiente esperando nfm_reply.
            read_idle_timeout_seconds_activity,
            start_or_signal_sales_workflow_activity,
            schedule_remarketing_workflow_activity,
            # ADR-2026-05-20: declarative orchestration activities.
            write_pending_handoff_activity,
            dispatch_event_activity,
            # HU-002: render UI intents emitidos por decision tools (post-LLM).
            flush_pending_ui_intents_activity,
            # Fix integridad orden↔tag: red de seguridad determinística que
            # garantiza el cierre "pago pendiente" + escalación tras un
            # register_order exitoso aunque el LLM no emita el tag/escalación.
            ensure_payment_pending_closure_activity,
            # Patrón A (CONFIRMADO_SIN_DATOS): garantiza la escalación a humano
            # cuando el LLM marca un closing tag que la exige pero no escala.
            ensure_closing_escalation_activity,
            # HU-002 / A.5: transcripción de audio inbound (Groq/OpenAI).
            transcribe_audio_activity,
            # HU dialecto colombiano: hora de Bogotá + saludo apropiado
            # inyectado al plugin_context. Disponible para uso desde el
            # workflow si se requiere recomputar la hora mid-session
            # (ghosting trigger, handoff resume). El path normal del
            # cliente entrante usa el helper puro `context.py` desde
            # `load_or_start_sales_session.py`.
            compute_bogota_context_activity,
            # HU-WA24H-001 Sprint CAPI: Meta Conversions API event
            # dispatch (Lead / Purchase) en cierre de episode. Disparado
            # por el workflow vía `_map_closing_tag_to_capi_event`. Tiene
            # guards internos completos — skip silencioso si no aplica.
            send_capi_event_activity,
        ],
        # OTel obs: el TracingInterceptor (en get_temporal_client) crea spans
        # dentro del workflow sandbox → necesita opentelemetry como passthrough
        # o rompe con RestrictedWorkflowAccessError. Ver otel_workflow_runner.
        workflow_runner=otel_workflow_runner(),
    )

    logger.info("😎 Sales Agent En Vivo. Escuchando la cola exclusiva: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
