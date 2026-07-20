"""PostSaleReturnWorkflow — un ciclo diario del scheduler post-venta.

scan del vault (COMPRA_EXITOSA en humano) → una activity por sesión que
verifica que no haya robot corriendo y aplica la mutación del botón
"devolver al robot" → summary. La cadencia la da un Temporal Schedule
(1x/día, ver `workers/post_sale_return.py`); el workflow es one-shot.

R-DET: cero I/O acá — todo side-effect es activity. Una sesión que falla
tras sus retries cuenta como `failed` y NO tumba el ciclo (las demás se
procesan; el próximo ciclo la reintenta porque su estado no cambió).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.post_sale_return.activities import (
        RESULT_RETURNED,
        RESULT_SKIPPED_ORDER_NOT_DELIVERED,
        RESULT_SKIPPED_ORDER_UNKNOWN,
        RESULT_SKIPPED_ROBOT_RUNNING,
        RESULT_SKIPPED_STATE_CHANGED,
        return_post_sale_session_to_sales_activity,
        scan_post_sale_human_sessions_activity,
    )


@workflow.defn(name="PostSaleReturnWorkflow")
class PostSaleReturnWorkflow:
    @workflow.run
    async def run(self) -> dict:
        session_ids: list[str] = await workflow.execute_activity(
            scan_post_sale_human_sessions_activity,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        summary: dict = {
            "scanned": len(session_ids),
            "returned": 0,
            "skipped_robot_running": 0,
            "skipped_state_changed": 0,
            "skipped_order_not_delivered": 0,
            "skipped_order_state_unknown": 0,
            "failed": 0,
            "returned_sessions": [],
        }
        for session_id in session_ids:
            try:
                result = await workflow.execute_activity(
                    return_post_sale_session_to_sales_activity,
                    args=[session_id],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError:
                workflow.logger.warning(
                    "PostSaleReturn: %s falló tras retries — sigue el ciclo.",
                    session_id,
                )
                summary["failed"] += 1
                continue
            if result == RESULT_RETURNED:
                summary["returned"] += 1
                summary["returned_sessions"].append(session_id)
            elif result == RESULT_SKIPPED_ROBOT_RUNNING:
                summary["skipped_robot_running"] += 1
            elif result == RESULT_SKIPPED_STATE_CHANGED:
                summary["skipped_state_changed"] += 1
            elif result == RESULT_SKIPPED_ORDER_NOT_DELIVERED:
                summary["skipped_order_not_delivered"] += 1
            elif result == RESULT_SKIPPED_ORDER_UNKNOWN:
                summary["skipped_order_state_unknown"] += 1
            else:  # resultado desconocido = bug visible, no silencio
                workflow.logger.warning(
                    "PostSaleReturn: resultado desconocido %r para %s.",
                    result,
                    session_id,
                )
                summary["failed"] += 1
        workflow.logger.info("PostSaleReturn summary: %s", summary)
        return summary
