from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.core.infrastructure.whatsapp.activities import send_whatsapp_message_activity
    from src.core.infrastructure.temporal.dispatcher_activities import (
        schedule_remarketing_workflow_activity,
        start_or_signal_sales_workflow_activity,
    )
    from src.core.workflow_helpers import PendingMessage, run_agent_turn
    from src.domains.sales_whatsapp.activities import decide_ghosting_action

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
    async def run(self, input: SessionInput) -> None:
        turn_count = input.turn_count

        while True:
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending) > 0,
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(f"Ghosting detectado para sesión {input.session_id}. Inyectando trigger de auto-etiquetado.")
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
                    result = await run_agent_turn(input, msg)
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
                            args=[input.session_id, result.final_content],
                            start_to_close_timeout=timedelta(seconds=90),
                            retry_policy=RetryPolicy(maximum_attempts=2)
                        )

                    if self._force_shutdown:
                        workflow.logger.info(f"Auto-diagnóstico concluido. Apagando sesión {input.session_id} por abandono de usuario o transferencia.")
                        return

                finally:
                    self._processing = False

            # continue_as_new to keep history bounded
            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not self._pending:
                workflow.logger.info("Reached {} turns, continuing as new", _CONTINUE_AS_NEW_AFTER_TURNS)
                workflow.continue_as_new(
                    SessionInput(
                        session_id=input.session_id,
                        channel=input.channel,
                        chat_id=input.chat_id,
                        llm=input.llm,
                        workspace=input.workspace,
                        tool_definitions_json=input.tool_definitions_json,
                        turn_count=turn_count,
                    )
                )
