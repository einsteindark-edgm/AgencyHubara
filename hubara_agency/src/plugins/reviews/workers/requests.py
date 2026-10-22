"""Worker `requests` del plugin reviews.

Corre los dos flujos de las reseñas tras la entrega:
  * `ReviewRequestWorkflow` — uno por pedido entregado (lo arranca la
    transición `order_delivered_requests_review` del manifest de orders): la
    invitación a calificar, dentro de la ventana de 24 h si se puede (más
    barato, sin plantilla) o con la plantilla de marketing al día siguiente.
  * `ReviewSubmissionWorkflow` — uno por respuesta del formulario (lo arranca
    `review_submitted_starts_reviews` del manifest de chats): reseña, cupón
    personal, gracias con el botón a Google y, con 1–3 estrellas, el equipo.

Nace APAGADO por config (`REVIEW_REQUESTS_MODE=off`, Terraform): el worker
corre igual y los flujos terminan en `skip: disabled` sin escribirle a nadie.

Solo imports `src.sdk` + el propio plugin (P-28).
"""
from __future__ import annotations

import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.plugins.reviews.agent.activities import (
    check_delivery_activity,
    plan_step_activity,
    process_submission_activity,
    send_request_activity,
)
from src.plugins.reviews.agent.settings import ReviewSettings
from src.plugins.reviews.agent.requests.workflows import (
    ReviewRequestWorkflow,
    ReviewSubmissionWorkflow,
)
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.runtime import WORKER_GRACEFUL_SHUTDOWN, get_temporal_client, run_worker, setup_logging

setup_logging()


async def main() -> None:
    # P-21: un worker de plugin deshabilitado no debe ni conectarse a Temporal.
    ensure_plugin_enabled("reviews")
    client = await get_temporal_client()
    task_queue = get_task_queue("reviews", "requests")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ReviewRequestWorkflow, ReviewSubmissionWorkflow],
        activities=[
            plan_step_activity,
            send_request_activity,
            check_delivery_activity,
            process_submission_activity,
        ],
        graceful_shutdown_timeout=WORKER_GRACEFUL_SHUTDOWN,
    )
    logger.info("⭐ reviews worker arriba. Cola: '{}' · {}", task_queue, ReviewSettings.from_env().describe())
    await run_worker(worker)  # SIGTERM → apagado ordenado


if __name__ == "__main__":
    asyncio.run(main())
