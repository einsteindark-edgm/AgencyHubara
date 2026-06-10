"""``HubaraEtaSessionWorkflow`` — notificador puro de estado de pedidos.

**Cambio de comportamiento (2026-06-10, "convivencia ETA/Sales")**: el ETA ya
NO conversa ni posee la ruta. Empuja notificaciones de cambio de estado al
hilo de WhatsApp del cliente SIN robar el turno — Sales (o humano) siempre es
quien responde los inbounds; las preguntas de entrega las contesta Sales con
su tool ``check_order_status``. El manifest ya no declara ``owns_route`` (las
sesiones legacy con ``active_route=eta`` caen a Sales por el fallback del
router — ruta no registrada).

Disparador único (``notify_stage_change`` signal / start): lo emite el
dispatcher cuando la orders API publica ``OrderStageChangedEvent``. El workflow
genera, vía el LLM (plantillas cacheadas del workspace) o el template de
utilidad de Meta (fuera de ventana 24h), el mensaje de la etapa y lo envía.

Multi-pedido: una sola sesión de workflow por cliente notifica TODOS sus
pedidos en tránsito (el payload de cada signal trae su ``order_id``; el
tracking por pedido vive en ``metadata.eta_tracking.orders``).

NOTA DE DEPLOY (L-9): los runs ``eta-*`` viven días — TODO cambio que altere
la secuencia de comandos del workflow (nuevo ``execute_activity``, timer,
signal handler que emita comandos) va detrás de ``workflow.patched("<id>")``,
o el primer replay post-restart del worker rompe los runs vivos con
TMPRL1100 (nondeterminism). El sticky cache ENMASCARA el error hasta que el
worker se reinicia. Alternativa válida solo si se acepta perder los runs en
vuelo: drenarlos (terminate) como parte del rollout (precedente PR-D de
remarketing — el estado real vive en ``metadata.eta_tracking``, y
``signal_with_start`` los revive gratis al próximo evento).

DEHA: workflow = driving adapter. Toda I/O vía ``workflow.execute_activity``
(R-DET / R-HEARTBEAT).
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
    from src.platform.temporal.retry_policies import _LLM_OPTIONS
    from src.platform.whatsapp.activities import (
        send_whatsapp_message_activity,
        send_whatsapp_template_activity,
    )
    from src.platform.workflow_helpers import (
        PendingMessage,
        run_agent_turn,
    )
    from src.plugins.eta.agent.eta.activities import (
        all_trackings_terminal_activity,
        bootstrap_eta_session_activity,
        claim_eta_notification_activity,
        record_eta_notification_activity,
        start_eta_tracking_activity,
    )
    from src.plugins.eta.agent.eta.contracts import EtaSessionInput
    from src.plugins.eta.agent.eta.prompts import (
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
        self._pending_stages: list[dict] = []              # cambios de estado a notificar
        self._last_response: str | None = None
        self._processing = False
        self._force_shutdown = False

    # ── Signals ──────────────────────────────────────────────────────────
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

        # Inicializa el tracking del pedido seed en el mapa multi-pedido.
        # NO toca active_route/tag — el ETA no posee la conversación.
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
        saw_terminal_stage = False

        while True:
            try:
                await workflow.wait_condition(
                    lambda: bool(self._pending_stages),
                    timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                workflow.logger.info(
                    f"ETA idle timeout para {session.session_id} — cerrando sesión."
                )
                return

            # Notificaciones de cambio de estado (única responsabilidad).
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
                if stage in ("delivered", "cancelled"):
                    saw_terminal_stage = True

            # Cierre proactivo: si acabamos de procesar un stage terminal y
            # TODOS los pedidos trackeados quedaron entregados/cancelados, el
            # workflow termina en vez de dormir el idle de 7 días. Revivir es
            # gratis: un pedido nuevo lo re-arranca vía signal_with_start (L-8).
            # ``patched``: este bloque agrega un execute_activity al loop de un
            # workflow de vida larga — sin el gate, el replay de runs nacidos
            # antes del deploy emite comandos que el historial viejo no tiene
            # (TMPRL1100, L-9: run 4d5e7baf quedó atascado tras un delivered).
            if (
                saw_terminal_stage
                and not self._pending_stages
                and workflow.patched("eta-proactive-close-v1")
            ):
                all_done = await workflow.execute_activity(
                    all_trackings_terminal_activity,
                    session.session_id,
                    **_FAST,
                )
                if all_done:
                    workflow.logger.info(
                        f"ETA: todos los pedidos de {session.session_id} en estado "
                        "terminal — cierre proactivo."
                    )
                    return
            saw_terminal_stage = False

            if self._force_shutdown:
                workflow.logger.info(
                    f"ETA cerrando sesión {session.session_id} (escalación)."
                )
                return

            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not (
                self._pending_stages
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
            await self._notify_free_form(session, order_id, stage, facts)
        else:
            await self._notify_template(session, order_id, stage, facts)

    async def _notify_free_form(
        self, session: SessionInput, order_id: str, stage: str, facts: dict
    ) -> None:
        """Notificación DENTRO de ventana: el LLM genera el mensaje (cacheado)."""
        synthetic = build_stage_notification_turn(
            stage=stage,
            customer_name=facts.get("customer_name", ""),
            order_display_id=facts.get("order_display_id", ""),
            total_label=facts.get("total_label", ""),
            pay_type=facts.get("pay_type", "confirmed"),
            delivery_window=facts.get("delivery_window"),
            items_label=facts.get("items_label", ""),
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
                args=[session.session_id, order_id, stage, result.final_content],
                **_FAST,
            )

    async def _notify_template(
        self, session: SessionInput, order_id: str, stage: str, facts: dict
    ) -> None:
        """Notificación FUERA de ventana: template de utilidad aprobado.

        ``send_whatsapp_template_activity`` ya maneja idempotencia + los errores
        Meta non-retryable (131008 = template no aprobado → ApplicationError que
        el wrapper del loop captura y loguea). Hasta que el operador apruebe
        ``order_status_utility_v1`` en Meta Business Manager, este envío fallará
        con 131008 — estado conocido y observable, NO un crash.
        """
        status_label = STAGE_LABELS.get(stage, stage)
        reference = facts.get("order_display_id") or "tu pedido"
        items_label = facts.get("items_label") or ""
        if items_label:
            # El cliente no sabe qué es "#6" — nombramos los productos en el
            # slot de referencia (texto libre del template, truncado corto).
            reference = f"{reference} ({items_label})"[:120]
        variables = {
            "customer_first_name": facts.get("customer_name") or "",
            "order_reference": reference,
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
                order_id,
                stage,
                f"[Notificación de estado enviada por template: {status_label}]",
            ],
            **_FAST,
        )
