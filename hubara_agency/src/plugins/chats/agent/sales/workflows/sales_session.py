from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.platform.orchestration import (
        dispatch_event_activity,
        envelope_for,
    )
    from src.platform.whatsapp.activities import send_whatsapp_message_activity
    from src.platform.temporal.dispatcher import (
        start_or_signal_sales_workflow_activity,
    )
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.platform.workflow_helpers import (
        PendingMessage,
        coalesce_pending,
        run_agent_turn,
    )
    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.platform.whatsapp.activities import send_typing_indicator_activity
    from src.plugins.chats.agent.sales.activities import (
        bootstrap_sales_session_activity,
        decide_ghosting_action,
        ensure_closing_escalation_activity,
        ensure_payment_pending_closure_activity,
        flush_pending_ui_intents_activity,
        read_and_clear_pending_handoff_activity,
        read_idle_timeout_seconds_activity,
    )
    from src.platform.contracts import EpisodeClosedDecision
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.platform.whatsapp.capi_activity import (
        LEAD_CLOSING_TAGS,
        PURCHASE_CLOSING_TAGS,
        send_capi_event_activity,
    )
    from src.plugins.chats.shared.contracts.events import (
        EpisodeClosedEvent,
        SalesSessionCompletionEvent,
    )


# HU-WA24H-001 Sprint CAPI: helper module-level que mapea el closing_tag al
# CAPI event_name correspondiente. Vive afuera del workflow body porque los
# frozenset son inmutables y los lookups son O(1) determinísticos (R-DET
# safe). Devuelve None para tags que no disparan CAPI (RECHAZO / GHOSTED
# / TIMEOUT).
def _map_closing_tag_to_capi_event(closing_tag: str) -> str | None:
    """Map a sales episode closing tag to its CAPI event name, or None."""
    if closing_tag in PURCHASE_CLOSING_TAGS:
        return "Purchase"
    if closing_tag in LEAD_CLOSING_TAGS:
        return "Lead"
    return None


# Module-level anchor que evita que ruff strippee el import de
# `send_capi_event_activity` antes de que el workflow body lo referencie en
# `workflow.execute_activity(...)`. El workflow body usa el import
# directamente (no `_CAPI_ACTIVITY`), así que esta línea es defensa
# pura contra el formatter — se puede borrar cuando el workflow body
# garantice el uso.
_CAPI_ACTIVITY: object = send_capi_event_activity


# Fix integridad orden↔tag (patrón A): closing tags que EXIGEN escalación a
# humano. Si el LLM marca el tag (cierra el episodio) pero no llama
# `escalate_to_human`, el workflow garantiza la escalación correspondiente vía
# `ensure_closing_escalation_activity`. CONFIRMADO_PAGO_PENDIENTE NO va acá: lo
# cubre la red disparada por `order_registered_decision` (que además puede
# forzar el cierre del episodio). RECHAZO / COMPRA_EXITOSA no requieren humano.
_CLOSING_TAGS_REQUIRING_ESCALATION: dict[str, str] = {
    "CONFIRMADO_SIN_DATOS": "ORDER_PENDING_SHIPPING_DETAILS",
}


_CONTINUE_AS_NEW_AFTER_TURNS = 50

_IDLE_TIMEOUT = timedelta(minutes=1)

# Trailing debounce: tras el primer signal, esperamos hasta `_DEBOUNCE_SILENCE`
# de silencio antes de procesar. Cada signal nuevo resetea el timer. Si el
# cliente nunca para de escribir, el cap `_DEBOUNCE_MAX_WAIT` fuerza el
# procesamiento. Replay-safe: `workflow.wait_condition` + timeouts deterministicos.
_DEBOUNCE_SILENCE = timedelta(seconds=1.5)
_DEBOUNCE_MAX_WAIT = timedelta(seconds=12)


@workflow.defn(name="HubaraSalesSessionWorkflow")
class HubaraSalesSessionWorkflow:
    """Long-running session workflow with ghosting injection mechanism."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown: bool = False

    @workflow.signal
    async def send_message(
        self,
        message: str,
        media: list[str] | None = None,
        plugin_context: list[str] | None = None,
    ) -> None:
        self._pending.append(
            PendingMessage(message=message, media=media, plugin_context=plugin_context)
        )

    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    @workflow.run
    async def run(self, input: SalesSessionInput) -> None:
        # Bootstrap: construye SessionInput fuera del workflow (R-DET).
        # Reemplaza el `build_workspace_config` + `get_base_tools_registry`
        # que antes ejecutaba el caller (service.py / dispatcher_activities)
        # antes de `start_workflow`. Patron simetrico al de Remarketing (F6.1).
        # PR-A: pasamos el SalesSessionInput completo. El campo
        # `runtime_workspace_path` viaja para PR-B sin romper la signature de
        # la activity en futuras iteraciones.
        session: SessionInput = await workflow.execute_activity(
            bootstrap_sales_session_activity,
            input,
            **_LLM_OPTIONS,
        )
        turn_count = input.turn_count

        # Handoff Remarketing→Sales (Fix 3, gated): el dispatcher escribio
        # `pending_handoff_summary` en metadata.json al transferir. Lo leemos +
        # clearamos atomicamente y seedeamos `_pending` con un marker
        # `is_handoff=True`. El coalesce lo va a mover a plugin_context cuando
        # construya el prompt (no contamina el rol "user" de la conversacion
        # como hacia el legacy `[SISTEMA INTERNO]` signal).
        if workflow.patched("handoff-via-metadata-v1"):
            initial_handoff = await workflow.execute_activity(
                read_and_clear_pending_handoff_activity,
                session.session_id,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if initial_handoff:
                self._pending.append(
                    PendingMessage(message=initial_handoff, is_handoff=True)
                )

        while True:
            # Dynamic ghosting timeout (sesión c4e3416f, gated): por default
            # son 60s, pero si acabamos de enviar un WhatsApp Flow nativo y
            # estamos esperando el `nfm_reply`, se extiende hasta 10 min para
            # que el cliente tenga tiempo de llenar el formulario sin que se
            # dispare ghosting prematuramente (que arrancaría remarketing y
            # ruteo el nfm_reply ahí cuando llegue).
            if workflow.patched("dynamic-idle-timeout-v1"):
                idle_timeout_seconds = await workflow.execute_activity(
                    read_idle_timeout_seconds_activity,
                    session.session_id,
                    start_to_close_timeout=timedelta(seconds=5),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                effective_idle_timeout = timedelta(seconds=idle_timeout_seconds)
            else:
                # Pre-patch workflows en replay caen al timeout legacy fijo
                # para preservar determinismo (R-DET).
                effective_idle_timeout = _IDLE_TIMEOUT

            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=effective_idle_timeout,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(f"Ghosting detectado para sesión {session.session_id}. Inyectando trigger de auto-etiquetado.")
                ghost_trigger = await workflow.execute_activity(
                    decide_ghosting_action,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )

                self._pending.append(PendingMessage(message=ghost_trigger))
                self._force_shutdown = True

            # Handoff refresh per-iteration (Fix 3, gated): si Sales ya estaba
            # corriendo cuando el dispatcher escribio handoff, el bootstrap NO
            # se reejecuta. Leemos metadata aca para captar handoffs que
            # llegaron mientras el workflow procesaba un turno previo.
            if workflow.patched("handoff-refresh-per-iteration-v1"):
                handoff_refresh = await workflow.execute_activity(
                    read_and_clear_pending_handoff_activity,
                    session.session_id,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                if handoff_refresh:
                    self._pending.append(
                        PendingMessage(message=handoff_refresh, is_handoff=True)
                    )

            # Trailing debounce con reset (Fix 1, gated): tras el primer
            # signal, esperamos hasta `_DEBOUNCE_SILENCE` de silencio. Cada
            # signal nuevo resetea el timer. Cap absoluto `_DEBOUNCE_MAX_WAIT`
            # protege contra clientes que tipean sin parar. Workflows
            # pre-deploy caen al path legacy (un mensaje por turno) por
            # `workflow.patched()` — replay-safe.
            if workflow.patched("trailing-debounce-coalesce-v1"):
                debounce_start = workflow.now()
                while True:
                    snapshot_len = len(self._pending)
                    elapsed = workflow.now() - debounce_start
                    cap_remaining = _DEBOUNCE_MAX_WAIT - elapsed
                    if cap_remaining <= timedelta(0):
                        break
                    timeout = min(_DEBOUNCE_SILENCE, cap_remaining)
                    try:
                        await workflow.wait_condition(
                            lambda: len(self._pending) > snapshot_len,
                            timeout=timeout,
                        )
                        # llego algo nuevo → siguiente iter con snapshot fresco
                        # = reset implicito del timer
                    except asyncio.TimeoutError:
                        break  # silencio achieved

                batch = list(self._pending)
                self._pending.clear()
                msgs_to_process: list[PendingMessage] = [coalesce_pending(batch)]
            else:
                # Legacy: un mensaje a la vez. Solo para workflows pre-deploy.
                msgs_to_process = []
                while self._pending:
                    msgs_to_process.append(self._pending.pop(0))

            for msg in msgs_to_process:
                self._processing = True

                try:
                    # Typing indicator outbound (Fix 5, gated): mostrar
                    # "escribiendo..." al cliente. Best-effort, no bloquea
                    # el turno si la API de WhatsApp falla.
                    if workflow.patched("typing-indicator-v1"):
                        try:
                            await workflow.execute_activity(
                                send_typing_indicator_activity,
                                session.session_id,
                                start_to_close_timeout=timedelta(seconds=5),
                                retry_policy=RetryPolicy(maximum_attempts=1),
                            )
                        except Exception:
                            pass

                    result = await run_agent_turn(session, msg)
                    self._last_response = result.final_content
                    turn_count += 1

                    # ADR-001 + ADR-2026-05-20: si la tool emitio una decision,
                    # convertirla a un completion event y dispatchar por manifest.
                    # NO importar workflow classes de sibling agents (R-DIP #10).
                    #
                    # workflow.patched(): pre-deploy histories tienen
                    # `schedule_remarketing_workflow_activity` directo; el patched
                    # gate evita NondeterminismError al replay-arlos. Tras drain,
                    # `workflow.deprecate_patch("declarative-orchestration-v1")`.
                    if result.schedule_remarketing is not None:
                        if workflow.patched("declarative-orchestration-v1"):
                            # Level 3 declarative path: emit event, dispatcher routes via manifest.
                            await workflow.execute_activity(
                                dispatch_event_activity,
                                envelope_for(
                                    SalesSessionCompletionEvent(
                                        session_id=result.schedule_remarketing.session_id,
                                        tag="INTERESADO",
                                        motivo=result.schedule_remarketing.motivo,
                                        delay_seconds=result.schedule_remarketing.delay_seconds,
                                    ),
                                    source_plugin="chats",
                                    source_worker="sales",
                                ),
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                        else:
                            # Legacy path for pre-deploy workflows (replay-safe).
                            # Local import — only resolved when the legacy branch
                            # actually executes during replay.
                            from src.platform.temporal.dispatcher import (
                                schedule_remarketing_workflow_activity,
                            )
                            await workflow.execute_activity(
                                schedule_remarketing_workflow_activity,
                                result.schedule_remarketing,
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                    if result.transfer_decision is not None:
                        # Sales self-loop (sales workflow asegurándose de estar
                        # corriendo + escribir handoff metadata). NO cross-agent —
                        # se queda con el activity legacy. La activity en si fue
                        # refactorizada en ADR-2026-05-20 para usar get_workflow_name()
                        # internamente y no importar HubaraSalesSessionWorkflow.
                        await workflow.execute_activity(
                            start_or_signal_sales_workflow_activity,
                            result.transfer_decision,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    # RED DE SEGURIDAD orden↔tag (fix integridad): si el LLM
                    # registró una orden con éxito (`order_registered_decision`),
                    # garantizamos el cierre "pago pendiente" + la escalación
                    # AUNQUE el LLM no haya emitido `manage_conversation_tag` /
                    # `escalate_to_human`. La secuencia de cierre la decide el
                    # LLM en tool calls separadas (coordinadas solo por el
                    # prompt); Temporal reanuda el turno tras un crash pero NO
                    # fuerza al LLM a emitir las tools. Esta activity es
                    # idempotente: no pisa si el LLM ya cerró.
                    #
                    # workflow.patched(): workflows en vuelo pre-deploy NO
                    # tienen esta activity en su history — el gate evita
                    # NondeterminismError al replay-arlos. Tras el drain
                    # (idle 1min en Sales), `deprecate_patch`.
                    episode_closed_decision = result.episode_closed_decision
                    safety_net_escalated = False
                    if (
                        result.order_registered_decision is not None
                        and workflow.patched("ensure-order-closure-v1")
                    ):
                        closure = await workflow.execute_activity(
                            ensure_payment_pending_closure_activity,
                            args=[
                                result.order_registered_decision.session_id,
                                result.order_registered_decision.order_id,
                                result.order_registered_decision.motivo,
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        # Si la red cerró el episodio (el LLM no lo hizo —
                        # mutuamente excluyente con el path del LLM, porque si
                        # el LLM cerró, `closure.acted=False`), adoptamos su
                        # decisión para emitir EpisodeClosedEvent + CAPI abajo
                        # sin duplicar.
                        if closure.acted and episode_closed_decision is None:
                            episode_closed_decision = EpisodeClosedDecision(
                                session_id=result.order_registered_decision.session_id,
                                episode_id=closure.closed_episode_id,
                                closing_tag=closure.closing_tag,
                            )
                        if closure.escalated:
                            # La red escaló a humano → cerrar el workflow como
                            # cualquier escalation (cliente queda en cola
                            # humana; `active_route=humano` bloquea dispatch
                            # de mensajes futuros).
                            self._force_shutdown = True
                            safety_net_escalated = True

                    # HU-WA24H-001 Sprint 2: el episodio cerró con un
                    # CLOSING_TAG (vía `ManageConversationTagTool` O vía la red
                    # de seguridad de arriba). Emitir `EpisodeClosedEvent` para
                    # que el dispatcher manifest signale `cancel_watchdog` al
                    # watchdog del episodio. Gated bajo el mismo patch que el
                    # path declarativo — workflows en vuelo pre-deploy NO ven
                    # este branch (replay-safe).
                    if (
                        episode_closed_decision is not None
                        and workflow.patched("watchdog-event-emit-v1")
                    ):
                        await workflow.execute_activity(
                            dispatch_event_activity,
                            envelope_for(
                                EpisodeClosedEvent(
                                    session_id=episode_closed_decision.session_id,
                                    episode_id=episode_closed_decision.episode_id,
                                    closing_tag=episode_closed_decision.closing_tag,
                                ),
                                source_plugin="chats",
                                source_worker="sales",
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    # HU-WA24H-001 Sprint CAPI: cuando el episodio cierra,
                    # disparar el CAPI event correspondiente (Lead /
                    # Purchase) si aplica. La activity tiene guards
                    # internos completos (ctwa_clid presente + dentro de
                    # los 7d de attribution window, terminal_event check,
                    # idempotencia via event_id estable), así que si no
                    # corresponde, hace un "skipped_*" silencioso sin
                    # raise.
                    #
                    # Falla del CAPI NUNCA bloquea el workflow — la
                    # atribución a ads es secundaria al flujo del
                    # cliente. Catcheamos cualquier excepción y seguimos.
                    # La activity ya loguea el outcome (sent / skipped /
                    # failed) — observabilidad vive en logs + metadata.
                    #
                    # workflow.patched(): gating para evitar
                    # NondeterminismError al replay de workflows en vuelo
                    # pre-deploy. Cuando todos los workflows pre-CAPI
                    # hayan drainado (idle timeout 1min en sales),
                    # `workflow.deprecate_patch("capi-event-emit-v1")`.
                    if (
                        episode_closed_decision is not None
                        and workflow.patched("capi-event-emit-v1")
                    ):
                        capi_event_name = _map_closing_tag_to_capi_event(
                            episode_closed_decision.closing_tag
                        )
                        if capi_event_name is not None:
                            try:
                                await workflow.execute_activity(
                                    send_capi_event_activity,
                                    args=[
                                        episode_closed_decision.session_id,
                                        episode_closed_decision.episode_id,
                                        capi_event_name,
                                    ],
                                    start_to_close_timeout=timedelta(seconds=30),
                                    retry_policy=RetryPolicy(maximum_attempts=3),
                                )
                            except Exception as exc:  # noqa: BLE001
                                workflow.logger.warning(
                                    "CAPI dispatch falló (non-blocking): "
                                    f"session={episode_closed_decision.session_id} "
                                    f"event={capi_event_name} err={exc!r}"
                                )

                    # RED DE SEGURIDAD escalación (patrón A): un closing tag que
                    # EXIGE escalación a humano (ej. CONFIRMADO_SIN_DATOS) cerró
                    # el episodio, pero el LLM NO llamó `escalate_to_human`. El
                    # workflow garantiza la escalación faltante. Skip si ya se
                    # escaló (LLM o la red de pago pendiente). Gated por patch
                    # (replay-safe). CONFIRMADO_PAGO_PENDIENTE no entra al mapa:
                    # lo cubre la red de `order_registered_decision`.
                    if (
                        episode_closed_decision is not None
                        and not safety_net_escalated
                        and result.escalation_decision is None
                        and workflow.patched("ensure-closing-escalation-v1")
                    ):
                        _esc_reason = _CLOSING_TAGS_REQUIRING_ESCALATION.get(
                            episode_closed_decision.closing_tag
                        )
                        if _esc_reason is not None:
                            _escalated = await workflow.execute_activity(
                                ensure_closing_escalation_activity,
                                args=[
                                    episode_closed_decision.session_id,
                                    _esc_reason,
                                    "Cierre con datos de envío pendientes — "
                                    "un humano debe pedir los datos faltantes.",
                                ],
                                start_to_close_timeout=timedelta(seconds=15),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                            if _escalated:
                                self._force_shutdown = True
                                safety_net_escalated = True

                    # SALUDO DESCARTADO (bug run ddd0d472 / session-wa_573125671604):
                    # cuando el LLM emite texto client-facing JUNTO con una tool
                    # call (ej. "Buenos días. Bienvenido a *Hubara*..." encolando
                    # send_quick_replies), ese texto antes NO llegaba al cliente —
                    # el loop solo enviaba `final_content` (el content del último
                    # mensaje sin tools). `run_agent_turn` ahora los expone en
                    # `result.pre_tool_messages`; los mandamos como burbujas en
                    # orden ANTES del final_content y los persistimos al store del
                    # dashboard igual que final_content. Skip en force_shutdown
                    # (ghosting/escalation no envían texto).
                    #
                    # workflow.patched(): histories en vuelo pre-deploy NO tienen
                    # estos sends en su shape; el gate evita NondeterminismError al
                    # replay-arlas. Tras el drain (idle 1min en Sales), eliminar el
                    # if + `workflow.deprecate_patch("send-pre-tool-messages-v1")`.
                    if (
                        result.pre_tool_messages
                        and not self._force_shutdown
                        and workflow.patched("send-pre-tool-messages-v1")
                    ):
                        for pre_msg in result.pre_tool_messages:
                            await workflow.execute_activity(
                                send_whatsapp_message_activity,
                                args=[session.session_id, pre_msg],
                                start_to_close_timeout=timedelta(seconds=90),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                            if workflow.patched("persist-assistant-message-v1"):
                                await workflow.execute_activity(
                                    persist_assistant_message_activity,
                                    args=[session.session_id, pre_msg],
                                    start_to_close_timeout=timedelta(seconds=10),
                                    retry_policy=RetryPolicy(maximum_attempts=2),
                                )

                    # UN SOLO MENSAJE en pickers de variante (bug run fe86d4e4):
                    # cuando el LLM llama `present_variant_picker`, el render de
                    # texto del intent (intro + opciones + invitación a elegir)
                    # YA es el mensaje completo del agente. Si además mandáramos
                    # `final_content`, el cliente vería DOS burbujas repitiendo
                    # la misma pregunta. Suprimimos el send de texto en ese caso
                    # — el flush del intent (más abajo) entrega el picker como
                    # burbuja única. Gated por patch para no romper el replay de
                    # workflows en vuelo (R-DET): histories pre-deploy no tienen
                    # el marker → patched()=False → mandan el texto como antes.
                    suppress_text_for_picker = (
                        workflow.patched("suppress-text-when-variant-picker-v1")
                        and "present_variant_picker" in result.tools_used
                    )
                    if result.final_content and not self._force_shutdown:
                        # Evitamos enviar respuestas vacías o alucinar respuestas internas durante auto-cierres
                        if not suppress_text_for_picker:
                            await workflow.execute_activity(
                                send_whatsapp_message_activity,
                                args=[session.session_id, result.final_content],
                                start_to_close_timeout=timedelta(seconds=90),
                                retry_policy=RetryPolicy(maximum_attempts=2)
                            )
                        # Persistir la respuesta al JSONL DESPUES del send: si el
                        # send falla y retry, no contaminamos el log con mensajes
                        # que el cliente nunca vio. El dashboard lee este JSONL
                        # para mostrar el lado del agente en el panel central.
                        #
                        # workflow.patched(): los workflows ya en vuelo (history
                        # generado antes de este deploy) NO tienen la activity
                        # en su history; el patched gate evita NondeterminismError
                        # al replay-arlos. Workflows nuevos siempre ven True.
                        # Cuando el idle timeout (1min en Sales) garantiza que no
                        # quedan in-flight pre-patch, eliminar el if + el patch_id
                        # con `workflow.deprecate_patch()` en el deploy siguiente.
                        if workflow.patched("persist-assistant-message-v1"):
                            # `tools_used` (3er arg, opcional): evidencia para el
                            # juez del eval — sin esto el juez no ve que el turno
                            # llamó search_products / escalate_to_human y puntúa
                            # falsos negativos (caso ep_010, run fa1eb974).
                            # patched(): runs en vuelo replayean con el shape de
                            # 2 args de su history (L-9).
                            _persist_args: list = [
                                session.session_id, result.final_content
                            ]
                            if workflow.patched("persist-tools-used-v1"):
                                _persist_args.append(list(result.tools_used or []))
                            await workflow.execute_activity(
                                persist_assistant_message_activity,
                                args=_persist_args,
                                start_to_close_timeout=timedelta(seconds=10),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )

                    # HU-002: render UI intents que las decision tools encolaron.
                    # Las tools `present_*`, `request_*`, `react_to_message`,
                    # `send_contact_card`, `send_cta_url` escriben intents a
                    # `metadata.json[pending_ui_intents]`. Esta activity los
                    # dispatch a `send_*` del cliente WA después de que el
                    # texto del LLM ya se envió, para que el cliente vea el
                    # texto + componente visual juntos.
                    #
                    # workflow.patched(): histories pre-HU-002 no tienen este
                    # branch — el patched gate evita NondeterminismError al
                    # replay-ar workflows en vuelo del deploy anterior.
                    if (
                        not self._force_shutdown
                        and workflow.patched("flush-ui-intents-v1")
                    ):
                        await workflow.execute_activity(
                            flush_pending_ui_intents_activity,
                            args=[session.session_id],
                            start_to_close_timeout=timedelta(seconds=120),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )

                    # Escalation a humano: la tool ya escribio metadata
                    # (active_route=humano, tag=HUMANO). Mandado ya el mensaje
                    # de despedida del LLM (en el bloque de send_whatsapp de
                    # arriba), cerramos el workflow. NO programamos remarketing —
                    # escalation y remarketing son mutuamente excluyentes: un
                    # humano va a tomar el caso. Subsecuentes mensajes del
                    # cliente NO arrancan un workflow nuevo porque
                    # `LoadOrStartSalesSession` chequea `active_route==humano`
                    # y omite el dispatch.
                    #
                    # workflow.patched(): histories pre-deploy no tienen este
                    # branch en su shape; el gate evita NondeterminismError al
                    # replay-arlos. Tras el primer drain (ver
                    # `docs/refactor/PHASE6.md::Drain operativo`), eliminar el
                    # if + `workflow.deprecate_patch("sales-escalation-v1")`.
                    if (
                        result.escalation_decision is not None
                        and workflow.patched("sales-escalation-v1")
                    ):
                        # stdlib logging usa `%s`, NO `{}` — el msg pasa por
                        # `record.getMessage()` que hace `msg % args`. F-string
                        # formatea antes y evita el conflicto (mismo patron que
                        # las otras lineas de este file en lineas 84 y 174).
                        workflow.logger.info(
                            f"Sesion {session.session_id} escalada a humano "
                            f"(reason={result.escalation_decision.reason_category}). "
                            "Cerrando workflow."
                        )
                        self._force_shutdown = True

                    if self._force_shutdown:
                        # Cancel-shutdown si llegaron mensajes nuevos durante
                        # el procesamiento (Fix 3 / H3, gated). Antes se
                        # retornaba directo y signals entre `_force_shutdown=True`
                        # y `return` se perdian.
                        #
                        # IMPORTANTE (post-mortem run bc54cb93, 2026-05-25):
                        # NO cancelar shutdown si el motivo es escalation a
                        # humano. La escalation es definitiva — el cliente
                        # quedó en cola humana y `active_route=humano` ya
                        # bloquea el dispatch para mensajes futuros. Si
                        # llegan mensajes durante el turn-de-escalation,
                        # el LLM puede emitir respuestas "del pensamiento"
                        # (vio mensaje, pero no sabe que ya escaló), y eso
                        # ROMPE LA EXPERIENCIA: el cliente recibe texto
                        # extra post "te transfiero al humano".
                        #
                        # Cancel-shutdown sigue habilitado para el caso
                        # ghosting (cliente volvió a tiempo) — ahí SÍ
                        # queremos continuar la conversación.
                        #
                        # workflow.patched(): workflows en vuelo pre-fix
                        # podrían haber tomado la rama legacy (cancel-shutdown
                        # también para escalation). En replay caen acá con
                        # `is_escalation = False` para preservar su history.
                        # Workflows nuevos ven `True` y respetan escalation.
                        if workflow.patched("escalation-blocks-cancel-shutdown-v1"):
                            # La escalación de la RED DE SEGURIDAD orden↔tag es
                            # tan definitiva como la del LLM: el cliente quedó
                            # en cola humana para verificación de pago. NO
                            # cancelar el shutdown aunque lleguen mensajes.
                            is_escalation = (
                                result.escalation_decision is not None
                                or safety_net_escalated
                            )
                        else:
                            is_escalation = False  # legacy replay path
                        if (
                            self._pending
                            and not is_escalation
                            and workflow.patched("cancel-shutdown-on-new-pending-v1")
                        ):
                            workflow.logger.info(
                                f"Cancel-shutdown: llegaron {len(self._pending)} "
                                f"mensaje(s) nuevos durante el turno. Continuando."
                            )
                            self._force_shutdown = False
                        else:
                            if is_escalation and self._pending:
                                # Logueamos explícitamente para que quede
                                # rastro: mensajes que llegaron post-escalation
                                # NO se procesan (el humano los verá en el
                                # dashboard cuando tome la sesión).
                                workflow.logger.info(
                                    f"Escalation activa: descartando "
                                    f"{len(self._pending)} mensaje(s) que "
                                    "llegaron durante el turno de escalation. "
                                    "El humano los retoma desde el dashboard."
                                )
                            workflow.logger.info(f"Auto-diagnóstico concluido. Apagando sesión {session.session_id} por abandono de usuario o transferencia.")
                            return

                finally:
                    self._processing = False

            # continue_as_new to keep history bounded
            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                # F-string: stdlib logging usa `%s`, no `{}`; el formato previo
                # `"Reached {} turns", N` lanzaba TypeError al disparar (bug
                # latente que solo se activaba al pasar 50 turnos).
                workflow.logger.info(
                    f"Reached {_CONTINUE_AS_NEW_AFTER_TURNS} turns, continuing as new"
                )
                workflow.continue_as_new(
                    SalesSessionInput(
                        session_id=session.session_id,
                        turn_count=turn_count,
                    )
                )
