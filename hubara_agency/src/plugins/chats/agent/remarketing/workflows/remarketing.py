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
    from src.platform.temporal.activities import (
        check_remarketing_eligibility,
        claim_conversation_routing,
        read_workspace_memory_activity,
    )
    from src.platform.whatsapp.activities import (
        check_reengagement_policy_activity,
        send_whatsapp_message_activity,
    )
    from src.platform.temporal.dispatcher import (
        start_or_signal_sales_workflow_activity,
        write_pending_handoff_activity,
    )
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.sdk.agentkit import is_no_message_abstention, looks_like_admin_leak
    from src.platform.workflow_helpers import (
        PendingMessage,
        coalesce_pending,
        run_agent_turn,
    )
    from src.platform.contracts import TransferDecision
    from src.plugins.chats.agent.remarketing.activities import (
        bootstrap_remarketing_session_activity,
        build_remarketing_trigger_activity,
    )
    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.platform.whatsapp.activities import send_typing_indicator_activity
    from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput
    from src.plugins.chats.shared.contracts.events import (
        CustomerRepliedDuringRemarketingEvent,
    )
    from src.platform.constants import ROUTE_REMARKETING, ROUTE_VENTAS

_IDLE_TIMEOUT = timedelta(hours=24)

# Trailing debounce: ver doc en sales_session.py. Mismos valores en ambos
# workflows para consistencia de UX. Replay-safe (workflow.wait_condition +
# timeouts deterministicos).
_DEBOUNCE_SILENCE = timedelta(seconds=1.5)
_DEBOUNCE_MAX_WAIT = timedelta(seconds=12)


@workflow.defn(name="RemarketingWorkflow")
class RemarketingSessionWorkflow:
    """Long-running session workflow for Remarketing."""

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
        """Signal a new message into the session from the Webhook."""
        self._pending.append(
            PendingMessage(message=message, media=media, plugin_context=plugin_context)
        )

    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    async def _handoff_to_sales(self, *, session_id: str, summary: str) -> None:
        """Dispatch a handoff back to Sales.

        ADR-2026-05-20 (Level 3 declarative orchestration):
            1. Write ``pending_handoff_summary`` to session metadata (so Sales'
               bootstrap picks it up on cold start, or its per-iter refresh on
               warm).
            2. Emit ``CustomerRepliedDuringRemarketingEvent`` — the dispatcher
               consults the manifest and starts/ensures the Sales workflow
               running (via=ensure_running, target_workflow="HubaraSalesSessionWorkflow").

        Replay-safety: gated by ``workflow.patched("declarative-orchestration-v1")``.
        Pre-deploy histories take the legacy branch (direct
        ``start_or_signal_sales_workflow_activity`` call). After the 24h idle
        timeout drains all in-flight workflows, ``workflow.deprecate_patch``
        and inline this method.
        """
        if workflow.patched("declarative-orchestration-v1"):
            # Step 1: persist handoff context to metadata (generic activity,
            # no agent imports).
            await workflow.execute_activity(
                write_pending_handoff_activity,
                args=[session_id, summary],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            # Step 2: emit event → dispatcher routes via manifest.
            await workflow.execute_activity(
                dispatch_event_activity,
                envelope_for(
                    CustomerRepliedDuringRemarketingEvent(
                        session_id=session_id,
                        summary=summary,
                    ),
                    source_plugin="chats",
                    source_worker="remarketing",
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        else:
            # Legacy path for pre-deploy histories (replay-safe).
            forced_decision = TransferDecision(
                session_id=session_id,
                target_route=ROUTE_VENTAS,
                summary=summary,
            )
            await workflow.execute_activity(
                start_or_signal_sales_workflow_activity,
                forced_decision,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

    @workflow.run
    async def run(self, input: RemarketingSessionInput) -> None:
        session_id = input.session_id
        motivo = input.motivo
        workflow.logger.info(f"Activando Sesión Conversacional de Remarketing para session: {session_id}")

        # ── Eligibility gate (post-mortem run e688685d-c676-4e61-a152-b22ff49788db) ──
        # Antes de tocar NADA (no `claim_conversation_routing`, no mensajes), chequear
        # si el caso es elegible para remarketing. Caso bloqueante observado en
        # producción 2026-05-20: sales workflow escaló el caso a humano (active_route=
        # humano, tag=HUMANO) entre el momento que se programó este remarketing con
        # start_delay=60s y el momento en que arrancó. Sin este gate, el workflow
        # pisaba el routing y reactivaba la conversación violando la regla de negocio
        # "cuando hay humano en el caso, ningún bot interviene".
        #
        # Si la elegibilidad cambia, returnamos early SIN side-effects. El humano
        # decide vía dashboard cuándo devolver el control a los bots.
        #
        # workflow.patched gate (replay-safe para histories pre-deploy): workflows
        # ya en vuelo NO ejecutan este branch — ejecutan el path legacy.
        if workflow.patched("remarketing-eligibility-gate-v1"):
            eligibility = await workflow.execute_activity(
                check_remarketing_eligibility,
                args=[session_id],
                start_to_close_timeout=timedelta(seconds=15),
            )
            if not eligibility.eligible:
                workflow.logger.warning(
                    "RemarketingWorkflow aborted by eligibility gate "
                    "(session_id=%s, route=%s, tag=%s): %s",
                    session_id,
                    eligibility.current_route,
                    eligibility.current_tag,
                    eligibility.blocked_reason,
                )
                # Return early — NO escribimos metadata, NO mandamos mensajes,
                # NO contaminamos history con turns falsos. El workflow completa
                # cleanly y libera el workflow_id para futuras reactivaciones
                # (si el humano devuelve el control via dashboard).
                return

        # ── Policy gate (WS-B2, plan Window Strategist) ──
        # Re-validación con la CENTRAL antes de tocar al cliente: el intent
        # que disparó este workflow (agente GraphAgents / dashboard /
        # transition INTERESADO) es solo un hint — la autoridad de SI/CÓMO
        # tocar es `decide_reengagement` con el estado REAL del vault
        # (ventanas, tag, ganchos, quiet hours). Suprime → return early sin
        # side-effects, mismo contrato que el eligibility gate de arriba.
        #
        # workflow.patched: histories en vuelo (duermen hasta 24h, L-9) no
        # ejecutan este branch. Tras drain, deprecate_patch.
        if workflow.patched("reengagement-policy-gate-v1"):
            policy = await workflow.execute_activity(
                check_reengagement_policy_activity,
                args=[session_id],
                start_to_close_timeout=timedelta(seconds=15),
            )
            if not policy.allowed:
                workflow.logger.warning(
                    "RemarketingWorkflow suprimido por la central send_policy "
                    "(session_id=%s, reason=%s): %s",
                    session_id,
                    policy.suppress_reason,
                    policy.rationale,
                )
                return

        # Bootstrap: construye SessionInput fuera del workflow (R-DET).
        # Reemplaza el `build_workspace_config` + `get_base_tools_registry` que
        # antes vivian dentro del @workflow.run.
        # PR-A workspace refactor: la activity ahora toma el
        # `RemarketingSessionInput` completo en vez de `(session_id, motivo)`,
        # para llevar `runtime_workspace_path` a traves del boundary (R-JSON).
        # PR-B consumira ese campo en el body de la activity.
        input_data: SessionInput = await workflow.execute_activity(
            bootstrap_remarketing_session_activity,
            input,
            **_LLM_OPTIONS,
        )

        await workflow.execute_activity(
            claim_conversation_routing,
            args=[session_id, ROUTE_REMARKETING],
            start_to_close_timeout=timedelta(seconds=15),
        )

        memory_context = await workflow.execute_activity(
            read_workspace_memory_activity,
            args=[session_id],
            start_to_close_timeout=timedelta(seconds=15),
        )

        system_trigger_msg = await workflow.execute_activity(
            build_remarketing_trigger_activity,
            args=[motivo, memory_context],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        # PR-B: identidad / tono / catalogo viven en el workspace canonico
        # (`workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md` y
        # `workspace/skills/hubara_catalog/SKILL.md`), leidos por
        # `ContextBuilder.build_system_prompt` en `build_prompt`. Ya no se
        # forwardea `shared_brain/*.md` por `plugin_context` — el campo
        # sobrevive en `PendingMessage` para datos volatiles del turno
        # (A-MEM, snippets), no identidad. Ver `core/workflow_helpers.py:PendingMessage`.
        self._pending.append(PendingMessage(
            message=system_trigger_msg,
            plugin_context=None,
        ))

        messages_processed = 0
        while True:
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(f"Cliente no respondió al Remarketing en {session_id}. Apagando agente.")
                await workflow.execute_activity(
                    claim_conversation_routing,
                    args=[session_id, ROUTE_VENTAS],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                return

            # Trailing debounce con reset (Fix 1, gated): identico a Sales.
            # Cada signal nuevo resetea el timer; cap absoluto 12s.
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
                    except asyncio.TimeoutError:
                        break

                batch = list(self._pending)
                self._pending.clear()
                msgs_to_process: list[PendingMessage] = [coalesce_pending(batch)]
            else:
                msgs_to_process = []
                while self._pending:
                    msgs_to_process.append(self._pending.pop(0))

            for msg in msgs_to_process:
                messages_processed += 1
                self._processing = True

                try:
                    # Typing indicator outbound (Fix 5, gated): mostrar
                    # "escribiendo..." al cliente. Best-effort.
                    if workflow.patched("typing-indicator-v1"):
                        try:
                            await workflow.execute_activity(
                                send_typing_indicator_activity,
                                session_id,
                                start_to_close_timeout=timedelta(seconds=5),
                                retry_policy=RetryPolicy(maximum_attempts=1),
                            )
                        except Exception:
                            pass

                    # PR-B: `fallback_plugin_context=None`. La identidad /
                    # tono / catalogo del agente entra al system prompt via
                    # `ContextBuilder` desde `workspace/*.md` durante
                    # `build_prompt`, no por este parametro. El path Sales
                    # ya pasaba `None`; Remarketing se alinea aqui.
                    # Premortem A4 (run 5f43bcd0): los turnos de remarketing
                    # son proactivos — content vacío = abstención. NUNCA la
                    # disculpa fabricada "¡Perdón! Justo se me cortó..." (le
                    # llegaba de la nada a un cliente frío y encima simula la
                    # marca de "interrupción técnica" que el cierre de
                    # ghosting de sales busca). patched(): histories en vuelo
                    # replayean con la fabricación original.
                    result = await run_agent_turn(
                        input_data,
                        msg,
                        fallback_plugin_context=None,
                        fabricate_fallback_on_empty=not workflow.patched(
                            "remarketing-no-fabricated-fallback-v1"
                        ),
                    )
                    self._last_response = result.final_content

                    # Abstención explícita (incidente wa_573229041190, run
                    # 019f7234): el prompt ofrece el sentinel NO_MESSAGE para
                    # cuando el gancho ya no corresponde (cliente ya respondió
                    # / ya compró / conversación viva). Sin este canal, la
                    # deliberación del LLM ("no genero un nuevo mensaje…") se
                    # enviaba al cliente como mensaje. workflow.patched():
                    # histories en vuelo no tienen la rama.
                    abstained = False
                    if workflow.patched("no-message-abstention-v1"):
                        abstained = (
                            result.transfer_decision is None
                            and is_no_message_abstention(result.final_content)
                        )
                    # Última línea determinista (run 5f43bcd0 + premortem D1):
                    # deliberación/reporte administrativo en el gancho, SIN el
                    # sentinel, equivale a abstención — el texto jamás va al
                    # cliente y el routing vuelve a ventas por la rama de
                    # abstención de abajo. patched(): histories en vuelo que
                    # SÍ enviaron esa prosa replayean sin la rama.
                    if (
                        not abstained
                        and result.transfer_decision is None
                        and result.final_content
                        and workflow.patched("admin-text-guard-v1")
                        and looks_like_admin_leak(result.final_content)
                    ):
                        workflow.logger.warning(
                            "admin-text-guard: gancho con texto administrativo "
                            f"bloqueado (≈abstención): "
                            f"{result.final_content[:120]!r}"
                        )
                        abstained = True

                    # ADR-001 + ADR-2026-05-20: si la tool emitio una decision
                    # de transferir a Sales, convertirla a un CompletionEvent y
                    # dispatchar por manifest. NO importar workflow classes de
                    # sibling agents (R-DIP #10).
                    #
                    # workflow.patched(): histories pre-deploy tienen la llamada
                    # directa a `start_or_signal_sales_workflow_activity`. El
                    # patched gate evita NondeterminismError durante replay.
                    # Tras drain (idle_timeout=24h), `workflow.deprecate_patch(
                    # "declarative-orchestration-v1")`.
                    if result.transfer_decision is not None:
                        workflow.logger.info("Remarketing ha transferido la sesión de vuelta a Ventas. Fin de Remarketing Workflow.")
                        await self._handoff_to_sales(
                            session_id=session_id,
                            summary=result.transfer_decision.summary or "El cliente volvió a interactuar",
                        )
                        self._force_shutdown = True
                    elif messages_processed > 1 and not self._force_shutdown:
                        # Salvavidas DETERMINISTA: si el usuario respondio y el LLM no
                        # uso la tool de transferir, lo forzamos con una decision sintetica.
                        workflow.logger.info("Remarketing ignoró la transición. Forzando paso a Ventas de forma determinista.")
                        await self._handoff_to_sales(
                            session_id=session_id,
                            summary="Usuario respondió: " + str(msg.message)[:60],
                        )
                        self._force_shutdown = True

                    # TEXTO DESCARTADO (mismo bug del loop compartido run_agent_turn,
                    # run ddd0d472): el texto client-facing que el LLM emite JUNTO
                    # con una tool call se perdía — solo viajaba final_content. Lo
                    # enviamos como burbuja antes del final_content. Skip si
                    # _force_shutdown (transfer a Sales no manda texto).
                    # workflow.patched(): histories en vuelo no tienen estos sends.
                    if (
                        result.pre_tool_messages
                        and not self._force_shutdown
                        and not abstained
                        and workflow.patched("send-pre-tool-messages-v1")
                    ):
                        for pre_msg in result.pre_tool_messages:
                            await workflow.execute_activity(
                                send_whatsapp_message_activity,
                                args=[session_id, pre_msg],
                                start_to_close_timeout=timedelta(seconds=90),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                            if workflow.patched("persist-assistant-message-v1"):
                                await workflow.execute_activity(
                                    persist_assistant_message_activity,
                                    args=[session_id, pre_msg],
                                    start_to_close_timeout=timedelta(seconds=10),
                                    retry_policy=RetryPolicy(maximum_attempts=2),
                                )

                    if (
                        result.final_content
                        and not self._force_shutdown
                        and not abstained
                    ):
                        await workflow.execute_activity(
                            send_whatsapp_message_activity,
                            args=[session_id, result.final_content],
                            start_to_close_timeout=timedelta(seconds=90),
                            retry_policy=RetryPolicy(maximum_attempts=2)
                        )
                        # Persistir DESPUES del send (mismo razonamiento que en
                        # sales_session.py). workflow.patched() preserva
                        # determinismo para histories pre-deploy. Remarketing
                        # tiene idle de 24h, asi que el patch debe permanecer
                        # al menos ese tiempo antes de `deprecate_patch`.
                        if workflow.patched("persist-assistant-message-v1"):
                            await workflow.execute_activity(
                                persist_assistant_message_activity,
                                args=[session_id, result.final_content],
                                start_to_close_timeout=timedelta(seconds=10),
                                retry_policy=RetryPolicy(maximum_attempts=2),
                            )
                        workflow.logger.info(f"Remarketing respondió para sesión {session_id}.")

                    if abstained and not self._force_shutdown:
                        # El toque sobra: no enviar, no persistir (nada que
                        # contamine el turno siguiente de Sales), devolver el
                        # routing a ventas y terminar. Si el cliente escribió
                        # durante el turno, sus mensajes van a Sales via
                        # handoff (mismo patrón que el drain post-transfer).
                        drained = [p.message for p in self._pending if p.message]
                        self._pending.clear()
                        workflow.logger.info(
                            "Remarketing abstuvo (NO_MESSAGE): toque suprimido; "
                            f"{len(drained)} pendiente(s) para handoff."
                        )
                        if drained:
                            await self._handoff_to_sales(
                                session_id=session_id,
                                summary=(
                                    "Usuario respondió: "
                                    + "\n".join(drained)[:500]
                                ),
                            )
                        else:
                            await workflow.execute_activity(
                                claim_conversation_routing,
                                args=[session_id, ROUTE_VENTAS],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                        return

                    if self._force_shutdown:
                        # M1/L-13 (run 8894825b): en remarketing,
                        # `_force_shutdown=True` ⟺ "ya transferí a Sales" (sus
                        # 2 únicos set-sites son el transfer del LLM y el
                        # force-handoff determinista). Los mensajes que
                        # llegaron durante el procesamiento ("Dame 2") NO
                        # ganan otro turno LLM aquí: ese turno costaba ~9s,
                        # siempre terminaba en el force-handoff determinista,
                        # y mientras tanto Sales ya había respondido con
                        # contexto incompleto. Drenado directo a handoff.
                        if (
                            self._pending
                            and workflow.patched("drain-pending-to-handoff-v1")
                        ):
                            drained = [
                                p.message for p in self._pending if p.message
                            ]
                            self._pending.clear()
                            workflow.logger.info(
                                f"Drain post-transfer remarketing: "
                                f"{len(drained)} mensaje(s) → handoff a Sales "
                                f"sin turno LLM."
                            )
                            await self._handoff_to_sales(
                                session_id=session_id,
                                summary=(
                                    "Usuario respondió: "
                                    + "\n".join(drained)[:500]
                                ),
                            )
                            return
                        # Cancel-shutdown legacy (Fix 3 / H3, gated): solo
                        # para replay de histories pre-drain — re-abría el
                        # loop y procesaba los pendientes con turno LLM.
                        if (
                            self._pending
                            and workflow.patched("cancel-shutdown-on-new-pending-v1")
                        ):
                            workflow.logger.info(
                                f"Cancel-shutdown remarketing: llegaron "
                                f"{len(self._pending)} mensaje(s) nuevos. Continuando."
                            )
                            self._force_shutdown = False
                        else:
                            return

                finally:
                    self._processing = False
