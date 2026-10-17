"""Invitación a calificar un pedido entregado (un workflow por pedido).

Lo arranca la transición `order_delivered_requests_review` del manifest de
`orders` (`OrderStageChangedEvent` con `to_stage: delivered`), con id
`review-request-{order_id}`: un pedido = un flujo, aunque el evento llegue dos
veces. El cuerpo no hace I/O (R-DET): pregunta a `reviews_plan_step` qué toca
(enviar el formulario dentro de la ventana, la plantilla fuera de ella,
esperar o dejarlo), duerme lo que dice el dominio y envía una sola vez.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.reviews.agent.activities import (
        ReviewDeliveryInput,
        ReviewPlanInput,
        ReviewSendInput,
        ReviewSendOut,
        ReviewStepOut,
    )

#: Tope de despertares (≈ 48 h / 30 min + márgenes): nunca un loop infinito.
_MAX_STEPS = 200
#: Ninguna espera menor a un minuto (un dominio que diga 0 no hace spin).
_MIN_WAIT = timedelta(minutes=1)
#: Tras un fallo antes de hablar con Meta (o al decidir), cuánto esperar.
_RETRY_WAIT = timedelta(minutes=30)
#: Cuánto esperar el aviso de entrega de Meta (webhook de estados).
_DELIVERY_CHECK_WAIT = timedelta(minutes=10)


@dataclass(frozen=True)
class ReviewRequestInput:
    """Del `OrderStageChangedEvent` (el dispatcher pasa un dict: todo con
    default)."""

    session_id: str = ""
    order_id: str = ""
    #: Cuándo pasó a entregado (la edad de la invitación se cuenta desde acá).
    occurred_at_ms: int = 0


@workflow.defn(name="ReviewRequestWorkflow")
class ReviewRequestWorkflow:
    @workflow.run
    async def run(self, inp: ReviewRequestInput) -> str:
        delivered_at_ms = inp.occurred_at_ms or self._now_ms()
        for _ in range(_MAX_STEPS):
            try:
                step = await workflow.execute_activity(
                    "reviews_plan_step",
                    ReviewPlanInput(
                        session_id=inp.session_id,
                        order_id=inp.order_id,
                        delivered_at_ms=delivered_at_ms,
                        now_ms=self._now_ms(),
                    ),
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                    result_type=ReviewStepOut,
                )
            except ActivityError:
                # Decidir solo lee: un vault o un Medusa caídos un rato no
                # terminan la invitación (premortem P5); el tope de 48 h manda.
                await workflow.sleep(_RETRY_WAIT)
                continue
            action = step.action
            if action in ("send_flow", "send_template"):
                outcome = await self._send(inp, action)
                if outcome == "retry":
                    # No salió (falló antes de Meta) o Meta avisó que el
                    # formulario no llegó: se vuelve a planear.
                    await workflow.sleep(_RETRY_WAIT)
                    continue
                return outcome
            if action == "skip":
                return step.reason
            await workflow.sleep(max(timedelta(milliseconds=step.wait_ms), _MIN_WAIT))
        return "max_steps"

    async def _send(self, inp: ReviewRequestInput, action: str) -> str:
        try:
            out = await workflow.execute_activity(
                "reviews_send_request",
                ReviewSendInput(
                    session_id=inp.session_id,
                    order_id=inp.order_id,
                    action=action,
                    now_ms=self._now_ms(),
                ),
                start_to_close_timeout=timedelta(seconds=120),
                # Un envío no se reintenta: dos invitaciones son peores que
                # ninguna (la activity registra el fallo).
                retry_policy=RetryPolicy(maximum_attempts=1),
                result_type=ReviewSendOut,
            )
        except ActivityError:
            # Se cayó a mitad: no se sabe si salió. No se reintenta; queda
            # «sin confirmar» en el registro (premortem P5).
            return "send_unconfirmed"
        if out.status != "sent":
            return out.status
        # Meta suele aceptar y avisar después si no entregó (webhook de estados).
        await workflow.sleep(_DELIVERY_CHECK_WAIT)
        try:
            return await workflow.execute_activity(
                "reviews_check_delivery",
                ReviewDeliveryInput(session_id=inp.session_id, order_id=inp.order_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
                result_type=str,
            )
        except ActivityError:
            return "sent"

    @staticmethod
    def _now_ms() -> int:
        return int(workflow.now().timestamp() * 1000)
