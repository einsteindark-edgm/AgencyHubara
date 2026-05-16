from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.platform.temporal.activities import claim_conversation_routing, read_workspace_memory_activity
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

    @workflow.run
    async def run(self, input: RemarketingSessionInput) -> None:
        session_id = input.session_id
        motivo = input.motivo
        workflow.logger.info(f"Activando Sesión Conversacional de Remarketing para session: {session_id}")

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
                    result = await run_agent_turn(
                        input_data,
                        msg,
                        fallback_plugin_context=None,
                    )
                    self._last_response = result.final_content

                    # ADR-001: si la tool emitio una decision de transferir a Sales, ejecutarla
                    # via activity dispatcher (durable, retriable).
                    if result.transfer_decision is not None:
                        workflow.logger.info("Remarketing ha transferido la sesión de vuelta a Ventas. Fin de Remarketing Workflow.")
                        await workflow.execute_activity(
                            start_or_signal_sales_workflow_activity,
                            result.transfer_decision,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        self._force_shutdown = True
                    elif messages_processed > 1 and not self._force_shutdown:
                        # Salvavidas DETERMINISTA: si el usuario respondio y el LLM no
                        # uso la tool de transferir, lo forzamos con una decision sintetica.
                        workflow.logger.info("Remarketing ignoró la transición. Forzando paso a Ventas de forma determinista.")
                        forced_decision = TransferDecision(
                            session_id=session_id,
                            target_route=ROUTE_VENTAS,
                            summary="Usuario respondió: " + str(msg.message)[:60],
                        )
                        await workflow.execute_activity(
                            start_or_signal_sales_workflow_activity,
                            forced_decision,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        self._force_shutdown = True

                    if result.final_content and not self._force_shutdown:
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

                    if self._force_shutdown:
                        # Cancel-shutdown si llegaron mensajes nuevos durante
                        # el procesamiento (Fix 3 / H3, gated). Simetrico al
                        # de Sales.
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
