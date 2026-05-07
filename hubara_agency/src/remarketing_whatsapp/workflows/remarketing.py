from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput
    from src.core.activities import claim_conversation_routing, read_workspace_memory_activity
    from src.core.infrastructure.whatsapp.activities import send_whatsapp_message_activity
    from src.core.infrastructure.temporal.dispatcher_activities import (
        start_or_signal_sales_workflow_activity,
    )
    from src.core.infrastructure.temporal.retry_policies import _LLM_OPTIONS
    from src.core.workflow_helpers import PendingMessage, run_agent_turn
    from src.core.contracts import TransferDecision
    from src.domains.remarketing_whatsapp.activities import (
        bootstrap_remarketing_session_activity,
        build_remarketing_trigger_activity,
        load_remarketing_brain_activity,
    )
    from src.domains.remarketing_whatsapp.contracts import RemarketingSessionInput
    from src.core.constants import ROUTE_REMARKETING, ROUTE_VENTAS

_IDLE_TIMEOUT = timedelta(hours=24)


@workflow.defn(name="RemarketingWorkflow")
class RemarketingSessionWorkflow:
    """Long-running session workflow for Remarketing."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown: bool = False
        self._brain_cache: list[str] | None = None

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

    async def _ensure_brain(self) -> list[str]:
        if self._brain_cache is None:
            self._brain_cache = await workflow.execute_activity(
                load_remarketing_brain_activity,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        return self._brain_cache

    @workflow.run
    async def run(self, input: RemarketingSessionInput) -> None:
        session_id = input.session_id
        motivo = input.motivo
        workflow.logger.info(f"Activando Sesión Conversacional de Remarketing para session: {session_id}")

        # Bootstrap: construye SessionInput fuera del workflow (R-DET).
        # Reemplaza el `build_workspace_config` + `get_base_tools_registry` que
        # antes vivian dentro del @workflow.run.
        input_data: SessionInput = await workflow.execute_activity(
            bootstrap_remarketing_session_activity,
            args=[session_id, motivo],
            **_LLM_OPTIONS,
        )
        ws_path = input_data.workspace.path

        await workflow.execute_activity(
            claim_conversation_routing,
            args=[ws_path, ROUTE_REMARKETING],
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

        self._pending.append(PendingMessage(
            message=system_trigger_msg,
            plugin_context=await self._ensure_brain(),
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
                    args=[ws_path, ROUTE_VENTAS],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                return

            while self._pending:
                msg = self._pending.pop(0)
                messages_processed += 1
                self._processing = True

                try:
                    result = await run_agent_turn(
                        input_data,
                        msg,
                        fallback_plugin_context=await self._ensure_brain(),
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
                        workflow.logger.info(f"Remarketing respondió para sesión {session_id}.")

                    if self._force_shutdown:
                        return

                finally:
                    self._processing = False
