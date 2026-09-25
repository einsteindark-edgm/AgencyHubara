"""CampaignSendWorkflow — un envío de campaña, de punta a punta (R-DET).

Determinista: cero I/O acá — plan, sends, touches y resultado son activities.
Un destinatario que falla se contabiliza y NO tumba la campaña (el operador
ve `failed` en el resultado). "Programar" = arrancar este workflow con
`start_delay` (Temporal nativo) desde la API.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from src.plugins.marketing.agent.campaigns.activities import (
        PLAN_MAX_ATTEMPTS,
        load_campaign_send_plan_activity,
        mark_campaign_sending_activity,
        mark_marketing_opt_out_activity,
        prepare_campaign_carousel_activity,
        record_campaign_send_result_activity,
        stamp_campaign_touch_activity,
    )
    from src.sdk.messagingkit import is_meta_opt_out_failure

_FAST = timedelta(seconds=30)
#: El send real hace un POST a Graph con retries de red adentro del client.
_SEND_TIMEOUT = timedelta(seconds=60)
#: Hasta 10 lecturas del snapshot del catálogo.
_CAROUSEL_TIMEOUT = timedelta(seconds=60)
#: Retries acotados por destinatario: un número inválido no debe colgar la
#: campaña entera (los non-retryable de Meta cortan solos en el 1er intento).
_SEND_RETRY = RetryPolicy(maximum_attempts=3)


@workflow.defn(name="CampaignSendWorkflow")
class CampaignSendWorkflow:
    @workflow.run
    async def run(self, campaign_id: str, campaign_name: str) -> dict:
        plan = await workflow.execute_activity(
            load_campaign_send_plan_activity,
            campaign_id,
            start_to_close_timeout=_FAST,
            retry_policy=RetryPolicy(maximum_attempts=PLAN_MAX_ATTEMPTS),
        )
        if plan.blocked_reason:
            # El cupón ya no servía a la hora del disparo: no sale nada. La
            # activity dejó la campaña fallida con el motivo (no se pisa).
            return {
                "sent": 0,
                "failed": 0,
                "planned": 0,
                "opted_out": 0,
                "blocked_reason": plan.blocked_reason,
            }
        # Carrusel: las tarjetas (productos del catálogo de Meta) se arman
        # UNA vez y las mismas viajan a cada destinatario.
        cards: list = []
        if plan.carousel_handles:
            cards = await workflow.execute_activity(
                prepare_campaign_carousel_activity,
                campaign_id,
                start_to_close_timeout=_CAROUSEL_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        await workflow.execute_activity(
            mark_campaign_sending_activity,
            campaign_id,
            start_to_close_timeout=_FAST,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        sent = 0
        failed: list[str] = []
        opted_out: list[str] = []
        for recipient in plan.recipients:
            try:
                send_args: list = [
                    recipient.session_id,
                    plan.template_name,
                    recipient.variables,
                ]
                if cards:
                    send_args.append(cards)
                outcome = await workflow.execute_activity(
                    "send_whatsapp_template_activity",
                    args=send_args,
                    start_to_close_timeout=_SEND_TIMEOUT,
                    retry_policy=_SEND_RETRY,
                )
            except ActivityError as e:
                cause = e.cause
                if isinstance(cause, ApplicationError) and is_meta_opt_out_failure(
                    cause.type
                ):
                    # Baja hecha en WhatsApp (131050): se registra atribuida a
                    # ESTA campaña; no es un fallo ni se le vuelve a intentar.
                    await workflow.execute_activity(
                        mark_marketing_opt_out_activity,
                        args=[recipient.session_id, campaign_id],
                        start_to_close_timeout=_FAST,
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    opted_out.append(recipient.session_id)
                else:
                    failed.append(recipient.session_id)
                continue
            sent += 1
            # El id del mensaje va al touch: por él el webhook de estados anota
            # entregado, leído y precio de ESTA campaña (Ads, 2026-09-25).
            wa_message_id = (
                outcome.get("wa_message_id") if isinstance(outcome, dict) else None
            )
            await workflow.execute_activity(
                stamp_campaign_touch_activity,
                args=[recipient.session_id, campaign_id, campaign_name, wa_message_id],
                start_to_close_timeout=_FAST,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        result = {
            "planned": len(plan.recipients),
            "sent": sent,
            "failed": failed,
            "opted_out": opted_out,
            "skipped": [
                {"session_id": s.session_id, "reason": s.reason}
                for s in plan.skipped
            ],
            "unit_cost_usd_micros": plan.unit_cost_usd_micros,
            "spent_usd_micros": plan.unit_cost_usd_micros * sent,
        }
        await workflow.execute_activity(
            record_campaign_send_result_activity,
            args=[campaign_id, result],
            start_to_close_timeout=_FAST,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return {
            "sent": sent,
            "failed": len(failed),
            "planned": len(plan.recipients),
            "opted_out": len(opted_out),
        }
