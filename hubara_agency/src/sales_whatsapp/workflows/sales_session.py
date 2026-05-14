from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.platform.whatsapp.activities import send_whatsapp_message_activity
    from src.platform.temporal.dispatcher import (
        schedule_remarketing_workflow_activity,
        start_or_signal_sales_workflow_activity,
    )
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.platform.workflow_helpers import PendingMessage, run_agent_turn
    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.sales_whatsapp.activities import (
        bootstrap_sales_session_activity,
        decide_ghosting_action,
    )
    from src.sales_whatsapp.contracts import SalesSessionInput

_CONTINUE_AS_NEW_AFTER_TURNS = 50

_IDLE_TIMEOUT = timedelta(minutes=1)


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

        while True:
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=_IDLE_TIMEOUT,
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

            while self._pending:
                msg = self._pending.pop(0)
                self._processing = True

                try:
                    result = await run_agent_turn(session, msg)
                    self._last_response = result.final_content
                    turn_count += 1

                    # ADR-001: si la tool emitio una decision, ejecutarla via activity dispatcher.
                    if result.schedule_remarketing is not None:
                        await workflow.execute_activity(
                            schedule_remarketing_workflow_activity,
                            result.schedule_remarketing,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                    if result.transfer_decision is not None:
                        await workflow.execute_activity(
                            start_or_signal_sales_workflow_activity,
                            result.transfer_decision,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    if result.final_content and not self._force_shutdown:
                        # Evitamos enviar respuestas vacías o alucinar respuestas internas durante auto-cierres
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
                            await workflow.execute_activity(
                                persist_assistant_message_activity,
                                args=[session.session_id, result.final_content],
                                start_to_close_timeout=timedelta(seconds=10),
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
