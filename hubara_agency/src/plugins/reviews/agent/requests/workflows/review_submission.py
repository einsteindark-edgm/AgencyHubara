"""La respuesta del formulario de calificación (un workflow por mensaje).

Lo arranca la transición `review_submitted_starts_reviews` del manifest de
`chats` (`ReviewSubmittedEvent`), con id `review-submit-{wa_message_id}`:
Meta reintenta webhooks y el mismo mensaje no se procesa dos veces. La
activity es idempotente (una reseña y un cupón por pedido), así que se
reintenta por horas (revisión M2): una caída de Medusa de varios minutos no
puede tirar la calificación y el cupón prometido.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.reviews.agent.activities import ReviewSubmissionInput


@workflow.defn(name="ReviewSubmissionWorkflow")
class ReviewSubmissionWorkflow:
    @workflow.run
    async def run(self, inp: ReviewSubmissionInput) -> str:
        return await workflow.execute_activity(
            "reviews_process_submission",
            inp,
            start_to_close_timeout=timedelta(seconds=120),
            schedule_to_close_timeout=timedelta(hours=12),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=10),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=10),
                maximum_attempts=0,  # sin tope: manda schedule_to_close
            ),
            result_type=str,
        )
