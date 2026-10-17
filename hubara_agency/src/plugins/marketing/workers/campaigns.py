"""Worker `campaigns` del plugin marketing — corre el envío de campañas.

Composition root: cero lógica. Dos workers en el mismo proceso:

- `queue-marketing-campaigns`: `CampaignSendWorkflowV2` (envío por lotes,
  controlable) + `CampaignSendBatchWorkflow` (un lote) + sus activities, y el
  `CampaignSendWorkflow` viejo, sin cambios, para que las campañas
  programadas con ese tipo terminen bien (sus sends van por este queue).
- `queue-marketing-campaigns-sends`: solo la entrega del V2 (envío a Meta +
  touch, una activity por contacto), con su ritmo
  (`max_task_queue_activities_per_second`, lo aplica el servidor de Temporal a
  todo el queue): el plan y el vault no esperan turno.

Gotcha #6 del CLAUDE.md raíz: TODA clase/función registrada está importada
al top de este archivo (nada resuelto lazy dentro de lambdas).
"""
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from src.plugins.marketing.agent.campaigns.activities import (
    deliver_campaign_message_activity,
    finish_campaign_send_activity,
    load_campaign_batch_activity,
    load_campaign_send_plan_activity,
    mark_campaign_sending_activity,
    mark_marketing_opt_out_activity,
    prepare_campaign_carousel_activity,
    prepare_campaign_send_activity,
    record_campaign_progress_activity,
    record_campaign_send_result_activity,
    stamp_campaign_touch_activity,
)
from src.plugins.marketing.agent.campaigns.workflows.send import (
    CampaignSendWorkflow,
)
from src.plugins.marketing.agent.campaigns.workflows.send_v2 import (
    CampaignSendBatchWorkflow,
    CampaignSendWorkflowV2,
)
from src.plugins.marketing.domain.campaign_send import sends_task_queue
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.messagingkit import send_whatsapp_template_activity
from src.sdk.runtime import get_temporal_client, setup_logging

setup_logging()

#: Ritmo hacia Meta del envío de campañas (mensajes por segundo, para todo el
#: queue de envíos). Meta da 80 msg/s por número: 10 deja holgura para el
#: bot, que manda por el mismo número, y 6.000 contactos salen en ~10 min.
CAMPAIGN_SENDS_PER_SECOND = 10.0


def build_workers(client: Client) -> list[Worker]:
    queue = get_task_queue("marketing", "campaigns")
    return [
        Worker(
            client,
            task_queue=queue,
            workflows=[CampaignSendWorkflowV2, CampaignSendBatchWorkflow, CampaignSendWorkflow],
            activities=[
                prepare_campaign_send_activity,
                load_campaign_batch_activity,
                record_campaign_progress_activity,
                finish_campaign_send_activity,
                prepare_campaign_carousel_activity,
                # Solo el V1 (campañas programadas antes del V2):
                load_campaign_send_plan_activity,
                mark_campaign_sending_activity,
                mark_marketing_opt_out_activity,
                stamp_campaign_touch_activity,
                record_campaign_send_result_activity,
                send_whatsapp_template_activity,
            ],
        ),
        Worker(
            client,
            task_queue=sends_task_queue(queue),
            # Envío a Meta + touch, en UNA activity (solo acá: el ritmo del
            # queue es el ritmo hacia Meta).
            activities=[deliver_campaign_message_activity],
            max_task_queue_activities_per_second=CAMPAIGN_SENDS_PER_SECOND,
        ),
    ]


async def main() -> None:
    # P-21: un worker de plugin deshabilitado no debe ni conectarse a Temporal.
    ensure_plugin_enabled("marketing")
    client = await get_temporal_client()
    await asyncio.gather(*(worker.run() for worker in build_workers(client)))


if __name__ == "__main__":
    asyncio.run(main())
