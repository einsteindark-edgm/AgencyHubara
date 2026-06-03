"""``HubaraEtaSessionWorkflow`` — sesión long-lived del Agente ETA.

Notifica al cliente cada cambio de estado de su pedido y atiende sus
respuestas dentro de su rol (escalando a humano lo que se sale de él).

Disparadores:
  * **Outbound** (``notify_stage_change`` signal): lo emite el dispatcher cuando
    la orders API publica ``OrderStageChangedEvent``. El workflow genera, vía el
    LLM (con plantillas cacheadas del workspace), el mensaje de la etapa y lo
    envía por WhatsApp.
  * **Inbound** (``send_message`` signal): lo emite ``LoadOrStartSalesSession``
    cuando el cliente responde y ``active_route == eta``. El agente responde si
    la pregunta está en su alcance, o escala a humano / transfiere a Ventas.

Arranque: el dispatcher hace ``start_workflow_with_replace`` con
``EtaSessionInput(session_id, order_id, to_stage="preparing")``. ``run`` marca
``active_route=eta`` y procesa la notificación inicial.

DEHA: workflow = driving adapter. Toda I/O vía ``workflow.execute_activity``
(R-DET / R-HEARTBEAT). Greenfield → sin ``workflow.patched()`` (no hay histories
pre-deploy que preservar).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput

    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.platform.temporal.dispatcher import (
        start_or_signal_sales_workflow_activity,
    )
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.platform.whatsapp.activities import (
        send_typing_indicator_activity,
        send_whatsapp_message_activity,
        send_whatsapp_template_activity,
    )
    from src.platform.workflow_helpers import (
        PendingMessage,
        coalesce_pending,
        run_agent_turn,
    )
    from src.plugins.chats.agent.eta.activities import (
        bootstrap_eta_session_activity,
        claim_eta_notification_activity,
        record_eta_notification_activity,
        record_eta_reply_activity,
        start_eta_tracking_activity,
    )
    from src.plugins.chats.agent.eta.contracts import EtaSessionInput
    from src.plugins.chats.agent.eta.prompts import (
        STAGE_LABELS,
        build_stage_notification_turn,
    )


# El ciclo de un pedido (preparación → entrega) puede tomar días. El workflow
# duerme entre eventos; si pasa una semana sin cambios de estado ni inbounds,
# lo damos por cerrado (la entrega ya ocurrió o el pedido quedó inactivo).
_IDLE_TIMEOUT = timedelta(days=7)
_CONTINUE_AS_NEW_AFTER_TURNS = 50

_FAST = {"start_to_close_timeout": timedelta(seconds=10), "retry_policy": RetryPolicy(maximum_attempts=3)}
_ORDER = {"start_to_close_timeout": timedelta(seconds=30), "retry_policy": RetryPolicy(maximum_attempts=3)}

# Template de utilidad aprobado (catalog.yaml) para notificaciones de estado
# FUERA de la ventana de servicio 24h — la única vía que Meta permite para un
# mensaje proactivo fuera de ventana. Requiere aprobación en Meta Business
# Manager (runbook operacional, igual que los templates del watchdog).
_ORDER_STATUS_TEMPLATE = "order_status_utility_v1"


@workflow.defn(name="HubaraEtaSessionWorkflow")
class HubaraEtaSessionWorkflow:
    """Sesión de notificaciones de estado de pedido (Agente ETA)."""

    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []           # inbounds del cliente
        self._pending_stages: list[dict] = []              # cambios de estado a notificar
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown = False

    # ── Signals ──────────────────────────────────────────────────────────
    @workflow.signal
    async def send_message(
        self,
        message: str,
        media: list[str] | None = None,
        plugin_context: list[str] | None = None,
    ) -> None:
        """Inbound del cliente (lo rutea ``LoadOrStartSalesSession``)."""
        self._pending.append(
            PendingMessage(message=message, media=media, plugin_context=plugin_context)
        )

    @workflow.signal
    async def notify_stage_change(self, payload: dict) -> None:
        """Cambio de estado del pedido (lo emite el dispatcher por el manifest).

        ``payload`` = ``{"to_stage": str, "order_id": str}`` (del ``input_mapping``).
        """
        self._pending_stages.append(dict(payload))

    # ── Queries ──────────────────────────────────────────────────────────
    @workflow.query
    def get_last_response(self) -> str | None:
        return self._last_response

    @workflow.query
    def is_processing(self) -> bool:
        return self._processing

    # ── Run ──────────────────────────────────────────────────────────────
    @workflow.run
    async def run(self, input: EtaSessionInput) -> None:
        session: SessionInput = await workflow.execute_activity(
            bootstrap_eta_session_activity, input, **_LLM_OPTIONS
        )

        # Marca la conversación como ETA-owned (route=eta + tag=ETA) e inicializa
        # el tracking del pedido.
        await workflow.execute_activity(
            start_eta_tracking_activity,
            args=[session.session_id, input.order_id],
            **_FAST,
        )

        # Seed: la notificación de la etapa que disparó el arranque (preparing).
        if input.to_stage:
            self._pending_stages.append(
                {"to_stage": input.to_stage, "order_id": input.order_id}
            )

        turn_count = input.turn_count

        while True:
            try:
                await workflow.wait_condition(
                    lambda: bool(self._pending) or bool(self._pending_stages),
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(
                    f"ETA idle timeout para {session.session_id} — cerrando sesión."
                )
                return

            # 1) Notificaciones de cambio de estado (outbound) primero.
            while self._pending_stages and not self._force_shutdown:
                ev = self._pending_stages.pop(0)
                stage = str(ev.get("to_stage", ""))
                order_id = str(ev.get("order_id", input.order_id))
                if not stage:
                    continue
                turn_count += 1
                try:
                    await self._notify_stage(session, order_id, stage)
                except Exception as exc:  # noqa: BLE001 — una notif fallida no tumba la sesión
                    workflow.logger.warning(
                        f"ETA notify_stage falló (no-fatal): session={session.session_id} "
                        f"stage={stage} err={exc!r}"
                    )

            # 2) Inbounds del cliente.
            if self._pending and not self._force_shutdown:
                turn_count += 1
                await self._handle_inbound(session)

            if self._force_shutdown:
                workflow.logger.info(
                    f"ETA cerrando sesión {session.session_id} (escalación/transferencia)."
                )
                return

            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not (
                self._pending or self._pending_stages
            ):
                workflow.continue_as_new(
                    EtaSessionInput(
                        session_id=session.session_id,
                        order_id=input.order_id,
                        turn_count=turn_count,
                    )
                )

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _notify_stage(
        self, session: SessionInput, order_id: str, stage: str
    ) -> None:
        """Notifica un cambio de estado, respetando la ventana de servicio 24h.

        - DENTRO de ventana (cliente escribió en las últimas 24h): el LLM genera
          el mensaje (cálido + cacheado) y se envía como texto libre.
        - FUERA de ventana (caso común — un pedido tarda días): Meta SOLO permite
          un template de utilidad aprobado, así que enviamos
          ``order_status_utility_v1`` con los slots. Sin esto, Meta rechaza la
          notificación con error 131047 (mensaje fuera de ventana).
        """
        facts = await workflow.execute_activity(
            claim_eta_notification_activity,
            args=[session.session_id, order_id, stage],
            **_ORDER,
        )
        if facts is None:
            return  # ruta humano / order distinto / ya notificado → skip

        if facts.get("in_service_window"):
            await self._notify_free_form(session, stage, facts)
        else:
            await self._notify_template(session, stage, facts)

    async def _notify_free_form(
        self, session: SessionInput, stage: str, facts: dict
    ) -> None:
        """Notificación DENTRO de ventana: el LLM genera el mensaje (cacheado)."""
        synthetic = build_stage_notification_turn(
            stage=stage,
            customer_name=facts.get("customer_name", ""),
            order_display_id=facts.get("order_display_id", ""),
            total_label=facts.get("total_label", ""),
            pay_type=facts.get("pay_type", "confirmed"),
            delivery_window=facts.get("delivery_window"),
        )
        result = await run_agent_turn(session, PendingMessage(message=synthetic))
        self._last_response = result.final_content

        # Escalación durante una notificación (raro): el agente decidió pasar a
        # humano. Respetamos el cierre como en el path inbound.
        if result.escalation_decision is not None:
            self._force_shutdown = True

        if result.final_content and not self._force_shutdown:
            await workflow.execute_activity(
                send_whatsapp_message_activity,
                args=[session.session_id, result.final_content],
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            await workflow.execute_activity(
                persist_assistant_message_activity,
                args=[session.session_id, result.final_content],
                **_FAST,
            )
            await workflow.execute_activity(
                record_eta_notification_activity,
                args=[session.session_id, stage, result.final_content],
                **_FAST,
            )

    async def _notify_template(
        self, session: SessionInput, stage: str, facts: dict
    ) -> None:
        """Notificación FUERA de ventana: template de utilidad aprobado.

        ``send_whatsapp_template_activity`` ya maneja idempotencia + los errores
        Meta non-retryable (131008 = template no aprobado → ApplicationError que
        el wrapper del loop captura y loguea). Hasta que el operador apruebe
        ``order_status_utility_v1`` en Meta Business Manager, este envío fallará
        con 131008 — estado conocido y observable, NO un crash.
        """
        status_label = STAGE_LABELS.get(stage, stage)
        variables = {
            "customer_first_name": facts.get("customer_name") or "",
            "order_reference": facts.get("order_display_id") or "tu pedido",
            "status_label": status_label,
        }
        await workflow.execute_activity(
            send_whatsapp_template_activity,
            args=[session.session_id, _ORDER_STATUS_TEMPLATE, variables],
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            record_eta_notification_activity,
            args=[
                session.session_id,
                stage,
                f"[Notificación de estado enviada por template: {status_label}]",
            ],
            **_FAST,
        )

    async def _handle_inbound(self, session: SessionInput) -> None:
        """Procesa la(s) respuesta(s) del cliente: responder o escalar/transferir."""
        batch = list(self._pending)
        self._pending.clear()
        msg = coalesce_pending(batch)

        try:
            await workflow.execute_activity(
                send_typing_indicator_activity,
                session.session_id,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

        result = await run_agent_turn(session, msg)
        self._last_response = result.final_content

        if result.final_content and not self._force_shutdown:
            await workflow.execute_activity(
                send_whatsapp_message_activity,
                args=[session.session_id, result.final_content],
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            await workflow.execute_activity(
                persist_assistant_message_activity,
                args=[session.session_id, result.final_content],
                **_FAST,
            )

        # Anota la respuesta del cliente en el timeline (la dashboard la muestra
        # colgada del último stage; flagged=True cuando se escaló a humano).
        flagged = result.escalation_decision is not None
        flag = result.escalation_decision.summary if result.escalation_decision else None
        await workflow.execute_activity(
            record_eta_reply_activity,
            args=[session.session_id, msg.message, flagged, flag],
            **_FAST,
        )

        # Transferencia a Ventas: el cliente quiere comprar más. La tool ya
        # escribió route=ventas; aseguramos el Sales workflow corriendo + handoff.
        if result.transfer_decision is not None:
            await workflow.execute_activity(
                start_or_signal_sales_workflow_activity,
                result.transfer_decision,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            self._force_shutdown = True

        # Escalación a humano: la tool ya escribió route=humano + tag=HUMANO.
        # El mensaje de despedida del LLM ya se envió arriba; cerramos.
        if result.escalation_decision is not None:
            workflow.logger.info(
                f"ETA sesión {session.session_id} escalada a humano "
                f"(reason={result.escalation_decision.reason_category})."
            )
            self._force_shutdown = True
