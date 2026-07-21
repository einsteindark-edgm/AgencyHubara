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
renderiza el mensaje de la etapa con **plantillas deterministas**
(``render_stage_notification`` — texto fijo, sin LLM) DENTRO de la ventana 24h,
o con el template de utilidad de Meta FUERA de ventana, y lo envía. El LLM se
quitó del path de notificaciones (2026-06-11): la matriz de mensajes es finita
y todos los slots llegan estructurados, así que rellenarlos con el LLM solo
agregaba costo, latencia y una superficie de fallo (inventar "Hola Cliente" /
"pago confirmado"). El bootstrap del agente (workspace + LLM config) ya no se
ejecuta.

DEPLOY (este cambio): altera la secuencia de comandos del workflow (quita las
activities del LLM/bootstrap), así que los runs ``eta-*`` en vuelo deben
DRENARSE (terminate) en el rollout — el estado durable vive en
``metadata.eta_tracking`` y ``signal_with_start`` los revive gratis al próximo
evento de pedido (precedente: remarketing PR-D). NO se gateó con ``patched``
a propósito: mantener vivo el path LLM dejaría a los clientes en sesión activa
recibiendo el mensaje buggy hasta que su sesión termine.

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
    from src.platform.session_history.activities import (
        persist_assistant_message_activity,
    )
    from src.platform.whatsapp.activities import (
        send_whatsapp_message_activity,
        send_whatsapp_template_activity,
    )
    from src.plugins.eta.agent.eta.activities import (
        all_trackings_terminal_activity,
        claim_eta_notification_activity,
        record_eta_notification_activity,
        start_eta_tracking_activity,
    )
    from src.plugins.eta.agent.eta.contracts import EtaSessionInput
    from src.plugins.eta.agent.eta.prompts import (
        render_stage_notification,
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
# mensaje proactivo fuera de ventana. v2 = sin saludo por nombre (incidente
# 2026-07-21: el slot de nombre llegaba vacío por el placeholder Medusa y Meta
# rechazaba el envío con 131008 → notificación perdida).
_ORDER_STATUS_TEMPLATE = "order_status_utility_v2"


@workflow.defn(name="HubaraEtaSessionWorkflow")
class HubaraEtaSessionWorkflow:
    """Sesión de notificaciones de estado de pedido (Agente ETA)."""

    def __init__(self) -> None:
        self._pending_stages: list[dict] = []              # cambios de estado a notificar
        self._last_response: str | None = None
        self._processing = False

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
        # Notificador puro: las notificaciones se renderizan con plantillas
        # deterministas (``render_stage_notification``), no con el LLM. Por eso
        # ya NO hay bootstrap del agente (workspace + LLM config): el envío solo
        # necesita el ``session_id``.
        session_id = input.session_id

        # Inicializa el tracking del pedido seed en el mapa multi-pedido.
        # NO toca active_route/tag — el ETA no posee la conversación.
        await workflow.execute_activity(
            start_eta_tracking_activity,
            args=[session_id, input.order_id],
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
                    f"ETA idle timeout para {session_id} — cerrando sesión."
                )
                return

            # Notificaciones de cambio de estado (única responsabilidad).
            while self._pending_stages:
                ev = self._pending_stages.pop(0)
                stage = str(ev.get("to_stage", ""))
                order_id = str(ev.get("order_id", input.order_id))
                if not stage:
                    continue
                turn_count += 1
                try:
                    await self._notify_stage(session_id, order_id, stage)
                except Exception as exc:  # noqa: BLE001 — una notif fallida no tumba la sesión
                    workflow.logger.warning(
                        f"ETA notify_stage falló (no-fatal): session={session_id} "
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
                    session_id,
                    **_FAST,
                )
                if all_done:
                    workflow.logger.info(
                        f"ETA: todos los pedidos de {session_id} en estado "
                        "terminal — cierre proactivo."
                    )
                    return
            saw_terminal_stage = False

            if turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS and not (
                self._pending_stages
            ):
                workflow.continue_as_new(
                    EtaSessionInput(
                        session_id=session_id,
                        order_id=input.order_id,
                        turn_count=turn_count,
                    )
                )

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _notify_stage(
        self, session_id: str, order_id: str, stage: str
    ) -> None:
        """Notifica un cambio de estado, respetando la ventana de servicio 24h.

        - DENTRO de ventana (cliente escribió en las últimas 24h): renderizamos
          el mensaje con plantilla determinista y lo enviamos como texto libre.
        - FUERA de ventana (caso común — un pedido tarda días): Meta SOLO permite
          un template de utilidad aprobado, así que enviamos
          ``order_status_utility_v2`` con los slots. Sin esto, Meta rechaza la
          notificación con error 131047 (mensaje fuera de ventana).
        """
        facts = await workflow.execute_activity(
            claim_eta_notification_activity,
            args=[session_id, order_id, stage],
            **_ORDER,
        )
        if facts is None:
            return  # ruta humano / order distinto / ya notificado → skip

        if facts.get("in_service_window"):
            await self._send_text_notification(session_id, order_id, stage, facts)
        else:
            await self._notify_template(session_id, order_id, stage, facts)

    async def _send_text_notification(
        self, session_id: str, order_id: str, stage: str, facts: dict
    ) -> None:
        """Notificación DENTRO de ventana: texto fijo renderizado, sin LLM.

        ``render_stage_notification`` es determinista: elige la plantilla por
        (estado + tipo de pago real) y rellena los slots. El string resultante
        es lo que recibe el cliente, palabra por palabra.
        """
        message = render_stage_notification(
            stage=stage,
            customer_name=facts.get("customer_name", ""),
            order_display_id=facts.get("order_display_id", ""),
            total_label=facts.get("total_label", ""),
            pay_type=facts.get("pay_type", "confirmed"),
            payment_confirmed=facts.get("payment_confirmed", False),
            delivery_window=facts.get("delivery_window"),
            items_label=facts.get("items_label", ""),
        )
        if not message:
            return  # stage desconocido → nada que enviar

        self._last_response = message
        await workflow.execute_activity(
            send_whatsapp_message_activity,
            args=[session_id, message],
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        await workflow.execute_activity(
            persist_assistant_message_activity,
            args=[session_id, message],
            **_FAST,
        )
        await workflow.execute_activity(
            record_eta_notification_activity,
            args=[session_id, order_id, stage, message],
            **_FAST,
        )

    async def _notify_template(
        self, session_id: str, order_id: str, stage: str, facts: dict
    ) -> None:
        """Notificación FUERA de ventana: template de utilidad aprobado.

        Las variables las arma ``build_status_template_variables`` (pura):
        sin nombre del cliente, nunca vacías, dentro de max_length — los tres
        invariantes que Meta exige de un param de template. ``facts`` NO trae
        el nombre al slot: el template v2 saluda "Hola," a secas.

        ``send_whatsapp_template_activity`` ya maneja idempotencia + los
        errores Meta non-retryable (p.ej. 131008 = param vacío/faltante,
        132001 = template inexistente en la WABA) → ApplicationError que el
        wrapper del loop captura y loguea como no-fatal.
        """
        variables = build_status_template_variables(stage, facts)
        await workflow.execute_activity(
            send_whatsapp_template_activity,
            args=[session_id, _ORDER_STATUS_TEMPLATE, variables],
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            record_eta_notification_activity,
            args=[
                session_id,
                order_id,
                stage,
                "[Notificación de estado enviada por template: "
                f"{variables['status_label']}]",
            ],
            **_FAST,
        )
