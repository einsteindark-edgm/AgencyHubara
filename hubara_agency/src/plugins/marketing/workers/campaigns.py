"""Worker `campaigns` del plugin marketing — corre CampaignSendWorkflow.

Composition root: cero lógica. Registra el workflow de envío + sus
activities + la activity de platform que ejecuta el send real (vía SDK).
Gotcha #6 del CLAUDE.md raíz: TODA clase/función registrada está importada
al top de este archivo (nada resuelto lazy dentro de lambdas).
"""
import asyncio

from temporalio.worker import Worker

from src.plugins.marketing.agent.campaigns.activities import (
    load_campaign_send_plan_activity,
    mark_campaign_sending_activity,
    record_campaign_send_result_activity,
    stamp_campaign_touch_activity,
)
from src.plugins.marketing.agent.campaigns.workflows.send import (
    CampaignSendWorkflow,
)
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.messagingkit import send_whatsapp_template_activity
from src.sdk.runtime import get_temporal_client, setup_logging

setup_logging()


async def main() -> None:
    # P-21: un worker de plugin deshabilitado no debe ni conectarse a Temporal.
    ensure_plugin_enabled("marketing")
    client = await get_temporal_client()
    worker = Worker(
        client,
        task_queue=get_task_queue("marketing", "campaigns"),
        workflows=[CampaignSendWorkflow],
        activities=[
            load_campaign_send_plan_activity,
            mark_campaign_sending_activity,
            send_whatsapp_template_activity,
            stamp_campaign_touch_activity,
            record_campaign_send_result_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
