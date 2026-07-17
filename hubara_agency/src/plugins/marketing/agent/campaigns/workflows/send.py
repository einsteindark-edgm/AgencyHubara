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
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.marketing.agent.campaigns.activities import (
        load_campaign_send_plan_activity,
        mark_campaign_sending_activity,
        record_campaign_send_result_activity,
        stamp_campaign_touch_activity,
    )

_FAST = timedelta(seconds=30)
#: El send real hace un POST a Graph con retries de red adentro del client.
_SEND_TIMEOUT = timedelta(seconds=60)
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
        for recipient in plan.recipients:
            try:
                await workflow.execute_activity(
                    "send_whatsapp_template_activity",
                    args=[recipient.session_id, plan.template_name, recipient.variables],
                    start_to_close_timeout=_SEND_TIMEOUT,
                    retry_policy=_SEND_RETRY,
                )
            except ActivityError:
                failed.append(recipient.session_id)
                continue
            sent += 1
            await workflow.execute_activity(
                stamp_campaign_touch_activity,
                args=[recipient.session_id, campaign_id, campaign_name],
                start_to_close_timeout=_FAST,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        result = {
            "planned": len(plan.recipients),
            "sent": sent,
            "failed": failed,
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
        return {"sent": sent, "failed": len(failed), "planned": len(plan.recipients)}
