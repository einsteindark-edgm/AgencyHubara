"""Workflow de ventas V2 (motor de decisiones con Jev, fase F4 — diseño v2 §08).

Un tipo NUEVO (`HubaraSalesSessionWorkflowV2`) registrado al lado del V1, que
queda congelado. Lo arranca por nombre el registro de bots
(`sales/decisions/bots.py`): en el laboratorio los brazos B0 (reglas) y B
(Jev); en producción, solo cuando el operador lo enciende (techo de Terraform).

Por qué un tipo nuevo y no parches al V1: lo que el V1 decide por dentro (la
fuga en el texto final, el saludo, el portavelas, el rescate, la cobertura ②)
solo se cambia con un `workflow.patched` por regla. Este tipo nace sin
historia: toma cada rama del V1 en su camino MÁS NUEVO, deja las que solo
existían para re-jugar historias viejas y no necesita ni un `patched` propio
(los del turno compartido `run_agent_turn` siguen adentro del helper).

No tiene reglas de texto (un test lo exige por AST): el egreso lo decide el
motor en la activity `decide_egress`, que corre DENTRO del turno, antes de
grabarlo (gancho `egress` de `run_agent_turn`): el LLM recuerda lo que de
verdad salió. El workflow solo aplica lo grabado. `is_no_message_abstention`
sí está: es el centinela del protocolo `NO_MESSAGE`, no una lectura del texto.

Diferencias con el V1, a propósito (cada una con su test en
`tests/test_sales_workflow_v2.py`):
  1. el panel del dashboard muestra solo lo que de verdad salió;
  2. el texto se suprime por el selector de variantes solo si el selector
     SALIÓ (no rechazado): un selector rechazado dejaba al cliente sin nada;
  3. sin la ronda extra de la capa ② por palabras (quedan la nota ① y la
     verificación ③ por la fachada del motor);
  4. el egreso (preámbulo del modelo, destinatario, rescate, portavelas,
     saludo) lo decide el motor; con `reglas` el resultado es el del V1, y el
     historial del LLM guarda el texto que salió.

Ramas del V1 que no pasan (solo existían para re-jugar historias viejas): el
turno de a un mensaje sin debounce, las notas de ráfaga v1 y previas, el
timeout fijo de inactividad, los envíos de `pre_tool_messages` (el turno
compartido ya nunca los llena: default-deny), `schedule_remarketing` (ninguna
tool lo emite desde la limpieza post-PR#113) y la autotransferencia (la tool
no está registrada en el worker de ventas y el V2 arranca con las tool
definitions de hoy).

Reutiliza las funciones puras del V1 importándolas de su módulo (sin copiar):
la traza, las capas ①③, los topes del debounce, el mapa de CAPI.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.plugins.chats.agent.sales.activities import (
        apply_variant_enumeration_guard_activity,
        bootstrap_sales_session_activity,
        build_first_contact_greeting_activity,
        decide_ghosting_action,
        ensure_closing_escalation_activity,
        ensure_payment_pending_closure_activity,
        flush_pending_ui_intents_activity,
        persist_turn_trace_activity,
        read_and_clear_pending_handoff_activity,
        read_idle_timeout_seconds_activity,
        read_order_draft_note_activity,
    )
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput

    # Motor de decisiones: el workflow solo importa su fachada y aplica lo que
    # el motor dejó grabado (nota ①, verificación ③, veredictos del egreso).
    from src.plugins.chats.agent.sales.decisions.facade import (
        EgressInput,
        EgressOutput,
        PerceiveInput,
        PerceiveOutput,
        TurnDecisions,
        VerifyInput,
        VerifyOutput,
        complement_note_of,
        contract_policy_of,
        decide_egress_activity,
        delivered_components,
        perceive_burst_activity,
        plan_of,
        verify_coverage_activity,
    )
    from src.plugins.chats.agent.sales.turn_trace import (
        build_turn_payload,
        context_note_names,
    )

    # Funciones puras del V1 (sin copiar): traza, capas ①③, debounce y CAPI.
    from src.plugins.chats.agent.sales.workflows.sales_session import (
        _ACTING_MODES,
        _CLOSING_TAGS_REQUIRING_ESCALATION,
        _CONTINUE_AS_NEW_AFTER_TURNS,
        _DEBOUNCE_MAX_WAIT,
        _DEBOUNCE_SILENCE,
        _DEFAULT_PERCEPTION_PROFILE,
        _LAYER_MODES,
        _MAX_TURN_RESTARTS,
        _PERCEPTION_OPTIONS,
        _burst_messages,
        _clean_inbound_meta,
        _flush_outbound,
        _inbound_trace,
        _map_closing_tag_to_capi_event,
        _note_guard,
        _now_ms,
        _perception_settings,
        _perception_step,
        _reply_as_sent,
        _text_outbound,
        _verify_step,
    )
    from src.plugins.chats.shared.contracts.events import EpisodeClosedEvent
    from src.sdk.agentkit import (
        LLM_ACTIVITY_OPTIONS,
        EpisodeClosedDecision,
        InboxMsg,
        PendingMessage,
        coalesce_inbox,
        is_no_message_abstention,
        persist_assistant_message_activity,
        run_agent_turn,
    )
    from src.sdk.eventkit import dispatch_event_activity, envelope_for
    from src.sdk.messagingkit import (
        flush_capi_outbox_activity,
        send_capi_event_activity,
        send_typing_indicator_activity,
        send_whatsapp_message_activity,
    )


# El egreso le puede preguntar a Jev hasta cuatro cosas (cada una con su tope
# de 1,5 s y la regla de respaldo): margen amplio, y dos intentos.
_EGRESS_OPTIONS: dict[str, Any] = {
    "start_to_close_timeout": timedelta(seconds=20),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}
_VARIANT_PICKER = "present_variant_picker"


@workflow.defn(name="HubaraSalesSessionWorkflowV2")
class HubaraSalesSessionWorkflowV2:
    """Sesión de ventas V2: misma entrada, señales y consultas que el V1."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown: bool = False
        # Capas del turno con clasificador: modo y perfil de la última señal
        # que los trajo, y los asuntos que quedaron pendientes (③).
        self._perception_mode: str = "off"
        self._perception_profile: str = _DEFAULT_PERCEPTION_PROFILE
        self._pending_topics: list[str] = []

    @workflow.signal
    async def send_message(
        self,
        message: str,
        media: list[str] | None = None,
        plugin_context: list[str] | None = None,
        inbound_meta: Any = None,
    ) -> None:
        """Mensaje del cliente. `inbound_meta` (4.º argumento, opcional) trae
        `{wamid, ts_ms, kind, text}` y el modo de las capas. Tipado `Any` a
        propósito: un valor que no decodifique como el tipo anotado hace que
        Temporal DESCARTE la señal entera; acá se limpia sin fallar."""
        mode, profile = _perception_settings(inbound_meta)
        if mode is not None:
            self._perception_mode = mode
        if profile is not None:
            self._perception_profile = profile
        self._pending.append(
            PendingMessage(
                message=message,
                media=media,
                plugin_context=plugin_context,
                inbound_meta=_clean_inbound_meta(inbound_meta),
            )
        )

    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    async def _handoff_draft_context(self, session_id: str) -> list[str] | None:
        """plugin_context del turno de handoff: la nota del borrador del pedido
        (run 019f6db3: sin ella el LLM re-preguntaba lo ya elegido)."""
        draft_note = await workflow.execute_activity(
            read_order_draft_note_activity,
            session_id,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return [draft_note] if draft_note else None

    async def _read_handoff(self, session_id: str) -> str | None:
        return await workflow.execute_activity(
            read_and_clear_pending_handoff_activity,
            session_id,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

    async def _perceive(self, inp: PerceiveInput) -> PerceiveOutput:
        """Capa ①: nunca tumba el turno (fail-open)."""
        try:
            return await workflow.execute_activity(perceive_burst_activity, inp, **_PERCEPTION_OPTIONS)
        except Exception as exc:  # noqa: BLE001
            return PerceiveOutput(ok=False, profile=inp.profile, error=f"activity: {type(exc).__name__}")

    async def _verify(self, inp: VerifyInput) -> VerifyOutput:
        """Capa ③: sin verificación, el turno sale como hoy (`send`)."""
        try:
            return await workflow.execute_activity(verify_coverage_activity, inp, **_PERCEPTION_OPTIONS)
        except Exception as exc:  # noqa: BLE001
            return VerifyOutput(ok=False, decision="send", error=f"activity: {type(exc).__name__}")

    async def _decide_egress(self, session_id: str, final_text: str, context: dict[str, Any]) -> dict[str, Any]:
        """Gancho de egreso de `run_agent_turn`: los veredictos del motor. Si
        la activity no responde, el texto no sale (sin reglas acá no hay con
        qué decidir) y la traza lo dice (`egress_error`)."""
        portavelas = context.get("portavelas_included")
        raw_text = context.get("raw_text")
        inp = EgressInput(
            session_id=session_id,
            final_text=final_text,
            first_contact=bool(context.get("first_contact")),
            tools_used=[str(t) for t in context.get("tools_used") or []],
            outbound_tool_texts=[str(t) for t in context.get("outbound_tool_texts") or []],
            order_registered=bool(context.get("order_registered")),
            portavelas_included=portavelas if isinstance(portavelas, bool) else None,
            admin_turn=bool(context.get("admin_turn")),
            # Lo que el LLM escribió antes del saneador: el motor decide ahí
            # la muletilla de presentación (capacidad `preambulo`).
            raw_text=raw_text if isinstance(raw_text, str) else None,
        )
        try:
            out = await workflow.execute_activity(decide_egress_activity, inp, **_EGRESS_OPTIONS)
        except Exception as exc:  # noqa: BLE001 — el turno sigue, sin texto
            out = EgressOutput(llm_text=final_text, final_text=final_text, error=f"activity: {type(exc).__name__}")
        return dataclasses.asdict(out)

    def _coalesce_batch(self, batch: list[PendingMessage]) -> PendingMessage:
        """Una ráfaga → un turno, con la nota de ráfaga (v2: solo mensajes con
        texto y sin contexto repetido). Preserva `is_handoff` (L-12)."""
        inbox_batch = [
            InboxMsg(
                seq=i,
                wamid=(p.inbound_meta or {}).get("wamid"),
                text=p.message,
                ts_ms=(p.inbound_meta or {}).get("ts_ms") or 0,
                media=p.media,
                plugin_context=p.plugin_context,
                is_handoff=p.is_handoff,
            )
            for i, p in enumerate(batch, 1)
        ]
        return coalesce_inbox(inbox_batch, version=2)

    @workflow.run
    async def run(self, input: SalesSessionInput) -> None:
        session: SessionInput = await workflow.execute_activity(
            bootstrap_sales_session_activity,
            input,
            **LLM_ACTIVITY_OPTIONS,
        )
        turn_count = input.turn_count

        async def egress(final_text: str, context: dict[str, Any]) -> dict[str, Any]:
            return await self._decide_egress(session.session_id, final_text, context)

        # Handoff Remarketing→Ventas: viaja por metadata y entra como marcador.
        initial_handoff = await self._read_handoff(session.session_id)
        if initial_handoff:
            self._pending.append(
                PendingMessage(
                    message=initial_handoff,
                    is_handoff=True,
                    plugin_context=await self._handoff_draft_context(session.session_id),
                )
            )

        while True:
            # Inactividad dinámica (sesión c4e3416f): más larga si espera el
            # formulario nativo de WhatsApp.
            idle_timeout_seconds = await workflow.execute_activity(
                read_idle_timeout_seconds_activity,
                session.session_id,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=timedelta(seconds=idle_timeout_seconds),
                )
            except asyncio.TimeoutError:
                # L-12 (run 3607aecc): antes del ghosting, un handoff pendiente
                # (o una señal que llegó en la carrera del timeout) es un turno
                # normal y NO hay ghosting este ciclo.
                late_handoff = await self._read_handoff(session.session_id)
                if late_handoff:
                    self._pending.append(
                        PendingMessage(
                            message=late_handoff,
                            is_handoff=True,
                            plugin_context=await self._handoff_draft_context(session.session_id),
                        )
                    )
                elif not self._pending:
                    workflow.logger.info(
                        f"Ghosting detectado para sesión {session.session_id}. Inyectando trigger de auto-etiquetado."
                    )
                    # Cierre por abandono (F8): con la sesión, el motor puede
                    # decidir la etiqueta y el aviso le dice al LLM cuál usar.
                    ghost_trigger = await workflow.execute_activity(
                        decide_ghosting_action,
                        session.session_id,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    self._pending.append(PendingMessage(message=ghost_trigger, is_ghost_trigger=True))
                    self._force_shutdown = True

            # Handoff que llegó mientras se procesaba un turno anterior.
            handoff_refresh = await self._read_handoff(session.session_id)
            if handoff_refresh:
                self._pending.append(PendingMessage(message=handoff_refresh, is_handoff=True))

            # Debounce con reinicio: silencio de `_DEBOUNCE_SILENCE`, con tope.
            debounce_start = workflow.now()
            while True:
                snapshot_len = len(self._pending)
                cap_remaining = _DEBOUNCE_MAX_WAIT - (workflow.now() - debounce_start)
                if cap_remaining <= timedelta(0):
                    break
                try:
                    await workflow.wait_condition(
                        lambda: len(self._pending) > snapshot_len,
                        timeout=min(_DEBOUNCE_SILENCE, cap_remaining),
                    )
                except asyncio.TimeoutError:
                    break

            batch = list(self._pending)
            self._pending.clear()
            # Complemento de la capa ③: si el cliente escribió mientras tanto,
            # su mensaje manda y el complemento se descarta.
            if any(p.is_complement_trigger for p in batch):
                customers = [p for p in batch if not p.is_complement_trigger]
                batch = customers or [p for p in batch if p.is_complement_trigger][-1:]
            raw_batch = batch
            msg = batch[0] if len(batch) == 1 and batch[0].is_complement_trigger else self._coalesce_batch(batch)

            # Turno ADMIN estructural (run 5f43bcd0): con el trigger de
            # ghosting, el texto es reporte interno y jamás va al cliente.
            turn_is_admin = any(p.is_ghost_trigger for p in raw_batch)
            # Premisa invalidada antes del turno (premortem C2): un mensaje real
            # junto al trigger → se descarta el trigger y el turno es normal.
            if turn_is_admin and any(not p.is_ghost_trigger for p in raw_batch):
                workflow.logger.info(
                    "Mensaje del cliente en la ventana de ghosting: premisa invalidada pre-turno — trigger "
                    "dropeado, shutdown cancelado, turno normal."
                )
                raw_batch = [p for p in raw_batch if not p.is_ghost_trigger]
                msg = self._coalesce_batch(raw_batch)
                self._force_shutdown = False
                turn_is_admin = False
            admin_no_send = turn_is_admin

            self._processing = True
            try:
                if not admin_no_send:
                    # "escribiendo…": best-effort (no bloquea el turno).
                    try:
                        await workflow.execute_activity(
                            send_typing_indicator_activity,
                            session.session_id,
                            start_to_close_timeout=timedelta(seconds=5),
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                    except Exception:  # noqa: BLE001
                        pass

                turn_started_ms = _now_ms()
                trace_sent_texts: list[str] = []
                trace_guards: list[str] = []
                trace_suppressed: str | None = None
                trace_steps: list[dict] = []
                restarts = 0
                # Capas ①③ del motor (sin la ronda extra ② por palabras).
                is_complement = msg.is_complement_trigger
                turn_mode = (
                    self._perception_mode
                    if not admin_no_send and not msg.is_handoff and not is_complement
                    else "off"
                )
                layers = turn_mode in _LAYER_MODES
                if not layers:
                    turn_mode = "off"
                decided: TurnDecisions | None = None
                shadow_handle = None
                shadow_started_ms = 0
                if layers and turn_mode == "shadow":
                    shadow_burst = _burst_messages(raw_batch)
                    if shadow_burst:
                        # Sombra: en paralelo al LLM; no suma espera.
                        shadow_started_ms = _now_ms()
                        shadow_handle = workflow.start_activity(
                            perceive_burst_activity,
                            PerceiveInput(
                                session_id=session.session_id,
                                profile=self._perception_profile,
                                messages=shadow_burst,
                                pending=list(self._pending_topics),
                            ),
                            **_PERCEPTION_OPTIONS,
                        )
                while True:
                    if layers and turn_mode in _ACTING_MODES:
                        burst = _burst_messages(raw_batch)
                        if burst:
                            perceived_ms = _now_ms()
                            perceived = await self._perceive(
                                PerceiveInput(
                                    session_id=session.session_id,
                                    profile=self._perception_profile,
                                    messages=burst,
                                    pending=list(self._pending_topics),
                                )
                            )
                            trace_steps.append(_perception_step(perceived, perceived_ms, mode=turn_mode))
                            decided = perceived
                            if perceived.note:
                                trace_steps.append(
                                    {
                                        "kind": "plan",
                                        "at_ms": _now_ms(),
                                        "checklist": [
                                            {"topic": t.topic, "msg": t.msg, "p": t.p} for t in plan_of(perceived).topics
                                        ],
                                    }
                                )
                                msg = dataclasses.replace(
                                    msg, plugin_context=[*(msg.plugin_context or []), perceived.note]
                                )
                    # Corrientazo (run eda8d460): si el cliente escribe mientras
                    # el LLM piensa, el turno se recompone (con tope).
                    hni = (lambda: bool(self._pending)) if restarts < _MAX_TURN_RESTARTS else None
                    result = await run_agent_turn(
                        session,
                        msg,
                        has_new_input=hni,
                        admin_turn=admin_no_send,
                        align_history_with_episode=True,
                        egress=egress,
                        # Segunda puerta (F6): solo con el contrato de tools
                        # GRABADO por el motor; sin él, el turno de hoy.
                        turn_policy=contract_policy_of(decided),
                    )
                    trace_steps.extend(result.steps or [])
                    if result.interrupted:
                        restarts += 1
                        drained = list(self._pending)
                        self._pending.clear()
                        trace_steps.append(
                            {"kind": "restart", "at_ms": _now_ms(), "reason": "checkpoint_a", "attempt": restarts,
                             "drained": len(drained)}
                        )
                        raw_batch = [p for p in [*raw_batch, *drained] if not p.is_complement_trigger]
                        msg = self._coalesce_batch(raw_batch)
                        is_complement = False
                        # Run 48ec6df5: un corrientazo durante el turno de
                        # ghosting invalida su premisa: el cliente volvió.
                        if self._force_shutdown:
                            workflow.logger.info(
                                "Corrientazo durante turno de ghosting: el cliente volvió — cancelando el shutdown "
                                "programado."
                            )
                            self._force_shutdown = False
                            # Run 5f43bcd0: el re-run es un turno normal sobre
                            # lo que escribió el cliente, sin el trigger.
                            non_admin = [p for p in raw_batch if not p.is_ghost_trigger]
                            if non_admin:
                                raw_batch = non_admin
                                msg = self._coalesce_batch(raw_batch)
                                admin_no_send = False
                        continue
                    break
                self._last_response = result.final_content
                turn_count += 1
                turn_key = f"run:{workflow.info().run_id}/t:{turn_count}"
                # Los veredictos del motor para el texto final (grabados).
                verdicts: dict[str, Any] = result.egress or {}
                final_text = str(verdicts.get("final_text") or "")
                text_out = str(verdicts.get("text") or "")

                # Abstención explícita (NO_MESSAGE): no se envía ni se persiste.
                abstained = is_no_message_abstention(result.final_content)
                if abstained:
                    _note_guard(
                        trace_steps, trace_guards, "no_message", before=result.final_content, after="", v1=False
                    )

                # Red de seguridad orden↔tag: cierre "pago pendiente" +
                # escalación aunque el LLM no emita las tools (idempotente).
                episode_closed_decision = result.episode_closed_decision
                safety_net_escalated = False
                # Premortem C3: el shutdown de las redes se aplica DESPUÉS de
                # enviar (la despedida del turno sale primero).
                shutdown_after_send = False
                if result.order_registered_decision is not None:
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
                    if closure.acted:
                        _note_guard(trace_steps, trace_guards, "safety_net_order_closure")
                    if closure.acted and episode_closed_decision is None:
                        episode_closed_decision = EpisodeClosedDecision(
                            session_id=result.order_registered_decision.session_id,
                            episode_id=closure.closed_episode_id,
                            closing_tag=closure.closing_tag,
                        )
                    if closure.escalated:
                        shutdown_after_send = True
                        safety_net_escalated = True

                if episode_closed_decision is not None:
                    # El episodio cerró: el dispatcher cancela el watchdog.
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
                    # Evento de Meta (CAPI) del cierre: su falla nunca bloquea.
                    capi_event_name = _map_closing_tag_to_capi_event(episode_closed_decision.closing_tag)
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
                                f"session={episode_closed_decision.session_id} event={capi_event_name} err={exc!r}"
                            )
                    # Red de seguridad patrón A: el closing tag exige escalación
                    # y el LLM no la llamó.
                    esc_reason = _CLOSING_TAGS_REQUIRING_ESCALATION.get(episode_closed_decision.closing_tag)
                    if not safety_net_escalated and result.escalation_decision is None and esc_reason is not None:
                        escalated = await workflow.execute_activity(
                            ensure_closing_escalation_activity,
                            args=[
                                episode_closed_decision.session_id,
                                esc_reason,
                                "Cierre con datos de envío pendientes — un humano debe pedir los datos faltantes.",
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        if escalated:
                            _note_guard(trace_steps, trace_guards, "safety_net_closing_escalation")
                            shutdown_after_send = True
                            safety_net_escalated = True

                # Saludo de primer contacto (runs dc32f7fe / 3ce50ef3): lo pide
                # el motor (`greeting_needed`); acá solo la mecánica del turno.
                if (
                    result.first_contact
                    and not msg.is_handoff
                    and not self._force_shutdown
                    and not abstained
                    and not admin_no_send
                    and verdicts.get("greeting_needed")
                ):
                    greeting = await workflow.execute_activity(
                        build_first_contact_greeting_activity,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    _note_guard(trace_steps, trace_guards, "first_contact_greeting", before="", after=greeting)
                    greeting_delivered = await workflow.execute_activity(
                        send_whatsapp_message_activity,
                        args=[session.session_id, greeting],
                        start_to_close_timeout=timedelta(seconds=90),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    trace_steps.append(_text_outbound(greeting, greeting_delivered))
                    await workflow.execute_activity(
                        persist_assistant_message_activity,
                        args=[session.session_id, greeting],
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    trace_sent_texts.append(greeting)

                # Selector de variantes (bug run fe86d4e4): si SALIÓ, él es el
                # mensaje y el texto no se manda. Un selector rechazado no
                # mostró nada: el texto sale (diferencia 2 con el V1). Con
                # escalación, la despedida sale siempre (run 5ed9af2d).
                suppress_text_for_picker = (
                    _VARIANT_PICKER in delivered_components(result.tool_events)
                    and result.escalation_decision is None
                )
                # Guarda de enumeración de variantes (run 9bd495be): la activity
                # decide contra el catálogo y encola el selector.
                if (
                    result.final_content
                    and not suppress_text_for_picker
                    and _VARIANT_PICKER not in result.tools_used
                    and not self._force_shutdown
                    and not abstained
                    and not admin_no_send
                ):
                    replaced_by_picker = await workflow.execute_activity(
                        apply_variant_enumeration_guard_activity,
                        args=[session.session_id, result.final_content],
                        start_to_close_timeout=timedelta(seconds=15),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    if replaced_by_picker:
                        suppress_text_for_picker = True
                        _note_guard(
                            trace_steps, trace_guards, "variant_enumeration_guard",
                            before=result.final_content, after="",
                        )
                        trace_suppressed = "variant_enumeration_guard"
                if admin_no_send and result.final_content:
                    # El LLM escribió pese al silencio del turno admin: queda en
                    # la traza (no en el historial: el turno ya lo recortó).
                    _note_guard(
                        trace_steps, trace_guards, "admin_turn", before=result.final_content, after="", v1=False
                    )
                # Las guardas del egreso que actuaron, en su orden (portavelas,
                # texto administrativo, rescate). El rescate antes de grabar
                # también cuenta, como en el V1.
                for guard in verdicts.get("guards") or []:
                    if isinstance(guard, dict) and guard.get("name"):
                        _note_guard(
                            trace_steps, trace_guards, str(guard["name"]),
                            before=guard.get("before"), after=guard.get("after"),
                        )
                if result.salvaged_leak:
                    trace_guards.append("admin_text_salvaged")
                leak_blocked = bool(verdicts.get("blocked"))

                # Capa ③: antes de enviar, ¿la respuesta atiende cada asunto?
                verify_out: VerifyOutput | None = None
                if (
                    layers
                    and turn_mode in _ACTING_MODES
                    and decided is not None
                    and decided.topics
                    and not self._force_shutdown
                    and not admin_no_send
                ):
                    verify_reply = _reply_as_sent(
                        trace_sent_texts,
                        None if (leak_blocked or suppress_text_for_picker or abstained) else text_out,
                    )
                    verified_ms = _now_ms()
                    verify_out = await self._verify(
                        VerifyInput(
                            session_id=session.session_id,
                            profile=self._perception_profile,
                            messages=_burst_messages(raw_batch),
                            topics=list(decided.topics),
                            reply_text=verify_reply,
                            components=delivered_components(result.tool_events),
                        )
                    )
                    trace_steps.append(_verify_step(verify_out, verified_ms, applied=True))
                if (
                    text_out
                    and not self._force_shutdown
                    and not abstained
                    and not admin_no_send
                    and not leak_blocked
                ):
                    if not suppress_text_for_picker:
                        final_delivered = await workflow.execute_activity(
                            send_whatsapp_message_activity,
                            args=[session.session_id, text_out],
                            start_to_close_timeout=timedelta(seconds=90),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        trace_steps.append(_text_outbound(text_out, final_delivered))
                        trace_sent_texts.append(text_out)
                        # El panel del dashboard muestra solo lo que salió
                        # (diferencia 1 con el V1). `tools_used`: evidencia
                        # para el juez del eval (caso ep_010).
                        await workflow.execute_activity(
                            persist_assistant_message_activity,
                            args=[session.session_id, text_out, list(result.tools_used or [])],
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                    elif trace_suppressed is None:
                        # El selector ES el mensaje (run fe86d4e4).
                        _note_guard(
                            trace_steps, trace_guards, "variant_picker_text", before=text_out, after="", v1=False
                        )

                # UI intents que encolaron las tools (HU-002), después del texto.
                if not self._force_shutdown and not admin_no_send:
                    flush_report = await workflow.execute_activity(
                        flush_pending_ui_intents_activity,
                        args=[session.session_id],
                        start_to_close_timeout=timedelta(seconds=120),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    flush_step = _flush_outbound(flush_report)
                    if flush_step is not None:
                        trace_steps.append(flush_step)

                # Outbox de eventos de Meta del turno: su falla nunca bloquea.
                try:
                    await workflow.execute_activity(
                        flush_capi_outbox_activity,
                        args=[session.session_id],
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except Exception as exc:  # noqa: BLE001
                    workflow.logger.warning(
                        f"CAPI outbox flush falló (non-blocking): session={session.session_id} err={exc!r}"
                    )

                # Sombra, después del envío y del flush: solo queda en la traza.
                if shadow_handle is not None:
                    try:
                        shadow_out = await shadow_handle
                    except Exception as exc:  # noqa: BLE001 — fail-open
                        shadow_out = PerceiveOutput(
                            ok=False, profile=self._perception_profile, error=f"activity: {type(exc).__name__}"
                        )
                    trace_steps.append(_perception_step(shadow_out, shadow_started_ms, mode="shadow"))
                    shadow_plan = plan_of(shadow_out)
                    if shadow_plan.topics:
                        trace_steps.append(
                            {
                                "kind": "plan",
                                "at_ms": _now_ms(),
                                "applied": False,
                                "checklist": [{"topic": t.topic, "msg": t.msg, "p": t.p} for t in shadow_plan.topics],
                            }
                        )
                        shadow_verified_ms = _now_ms()
                        shadow_verify = await self._verify(
                            VerifyInput(
                                session_id=session.session_id,
                                profile=self._perception_profile,
                                messages=_burst_messages(raw_batch),
                                topics=list(shadow_out.topics),
                                reply_text=_reply_as_sent(trace_sent_texts, None),
                                components=delivered_components(result.tool_events),
                            )
                        )
                        trace_steps.append(_verify_step(shadow_verify, shadow_verified_ms, applied=False))
                if verify_out is not None:
                    if (
                        verify_out.decision == "complement"
                        and verify_out.missing
                        and decided is not None
                        and not self._pending
                        and not self._force_shutdown
                    ):
                        self._pending.append(
                            PendingMessage(
                                message=complement_note_of(decided.topics, verify_out),
                                is_complement_trigger=True,
                            )
                        )
                        for step in reversed(trace_steps):
                            if step.get("kind") == "verify":
                                step["complement_scheduled"] = True
                                break
                    self._pending_topics = list(verify_out.missing) if verify_out.decision == "pending" else []
                elif layers and turn_mode in _ACTING_MODES:
                    self._pending_topics = []

                # Traza del turno para el scorecard (HU-SC-0), con los
                # veredictos del egreso (solo el V2 los trae).
                if trace_suppressed is None and final_text and final_text not in trace_sent_texts:
                    if abstained:
                        trace_suppressed = "no_message"
                    elif admin_no_send:
                        trace_suppressed = "admin_turn"
                    elif leak_blocked:
                        trace_suppressed = "admin_text_guard"
                    elif suppress_text_for_picker:
                        trace_suppressed = "variant_picker"
                    elif self._force_shutdown:
                        trace_suppressed = "shutdown"
                    elif not text_out:
                        trace_suppressed = "egress_error"  # el motor no respondió
                is_ghost_turn = any(p.is_ghost_trigger for p in raw_batch)
                trace_payload = build_turn_payload(
                    trigger=(
                        "ghost" if is_ghost_turn
                        else "handoff" if msg.is_handoff
                        else "complement" if is_complement
                        else "customer"
                    ),
                    inbound_text=msg.message or "",
                    turn_started_ms=turn_started_ms,
                    first_contact=result.first_contact,
                    tool_events=list(result.tool_events),
                    discarded_narration=list(result.discarded_narration),
                    llm_text=final_text,
                    sent_texts=trace_sent_texts,
                    suppressed_reason=trace_suppressed,
                    guards=trace_guards,
                    steps=trace_steps,
                    turn_key=turn_key,
                    mode=turn_mode,
                    context_notes=context_note_names(msg.plugin_context),
                    inbound=_inbound_trace(list(raw_batch)),
                )
                trace_payload["egress"] = {
                    "verdicts": list(verdicts.get("verdicts") or []),
                    "error": verdicts.get("error"),
                }
                try:
                    await workflow.execute_activity(
                        persist_turn_trace_activity,
                        args=[session.session_id, json.dumps(trace_payload, ensure_ascii=False)],
                        start_to_close_timeout=timedelta(seconds=15),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except Exception as exc:  # noqa: BLE001
                    workflow.logger.warning(
                        f"turn-trace: no se persistió la traza (non-blocking): session={session.session_id} "
                        f"err={exc!r}"
                    )

                # Escalación a humano: la despedida ya salió; se cierra el
                # workflow (humano y remarketing son excluyentes).
                if result.escalation_decision is not None:
                    workflow.logger.info(
                        f"Sesion {session.session_id} escalada a humano "
                        f"(reason={result.escalation_decision.reason_category}). Cerrando workflow."
                    )
                    self._force_shutdown = True
                # Shutdown diferido de las redes de seguridad (C3).
                if shutdown_after_send:
                    self._force_shutdown = True

                if self._force_shutdown:
                    # La escalación (del LLM o de la red) es definitiva: los
                    # mensajes que llegaron durante ese turno los ve el humano.
                    is_escalation = result.escalation_decision is not None or safety_net_escalated
                    if self._pending and not is_escalation:
                        workflow.logger.info(
                            f"Cancel-shutdown: llegaron {len(self._pending)} mensaje(s) nuevos durante el turno. "
                            "Continuando."
                        )
                        self._force_shutdown = False
                    else:
                        if is_escalation and self._pending:
                            workflow.logger.info(
                                f"Escalation activa: descartando {len(self._pending)} mensaje(s) que llegaron "
                                "durante el turno de escalation. El humano los retoma desde el dashboard."
                            )
                        workflow.logger.info(
                            f"Auto-diagnóstico concluido. Apagando sesión {session.session_id} por abandono de "
                            "usuario o transferencia."
                        )
                        return
            finally:
                self._processing = False

            # continue_as_new para acotar la historia (el mismo tipo V2).
            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                workflow.logger.info(f"Reached {_CONTINUE_AS_NEW_AFTER_TURNS} turns, continuing as new")
                workflow.continue_as_new(SalesSessionInput(session_id=session.session_id, turn_count=turn_count))
