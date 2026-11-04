"""Procedencia de la fixture CONGELADA del gate `scorecard-bands-v1`.

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Ciclo Colossus F4 (2026-11-03): el barrido diario de las 23:00 (Bogotá) suma
un paso nuevo después del scorecard: las bandas por regla, que pueden abrir una
alerta. Es un comando nuevo en `SalesEvalWorkflow`: un run que estaba en vuelo
al desplegar tiene que terminar con el camino de antes. La history es la de
ANTES: dos episodios calificados y la eval legada de uno. Se genera con el
código actual y el gate forzado a «no» (`workflow.patched` devuelve False sin
grabar marcador: exactamente el camino del código anterior).

    cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy \\
        OTEL_SDK_DISABLED=true uv run python -m tests.fixtures.generate_sales_eval_prebands_fixture /tmp/out

Sesiones sintéticas `wa_100000000001` y `wa_100000000002`.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

# A nivel de módulo: Temporal resuelve los type hints de las activities fake
# contra estos globals (con `from __future__ import annotations`).
from src.plugins.chats.agent.sales_eval.evals.contracts import (
    ConversationEvalResult,
    EvalWindowInput,
    ScorecardSummary,
)

GATE = "scorecard-bands-v1"
QUEUE = "sales-eval-prebands"
FIXTURE = "history_sales_eval_prebands_v1.json"


async def _generate() -> tuple[str, list[str]]:
    from temporalio import activity, workflow
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales_eval.workflows.sales_eval import SalesEvalWorkflow

    calls: list[str] = []

    @activity.defn(name="select_scorecard_units")
    async def units(window: EvalWindowInput) -> list[str]:
        return ["wa_100000000001::ep_001", "wa_100000000002::ep_003"]

    @activity.defn(name="score_episode_scorecard")
    async def score(session_id: str, episode_id: str, with_judge: bool) -> ScorecardSummary:
        calls.append(f"scorecard {session_id}")
        return ScorecardSummary(session_id=session_id, episode_id=episode_id, verdict="PASA", stored=True)

    @activity.defn(name="select_conversations_to_eval")
    async def select(window: EvalWindowInput) -> list[str]:
        return ["wa_100000000001::ep_001"]

    @activity.defn(name="evaluate_sales_conversation")
    async def evaluate(unit_id: str, window: EvalWindowInput) -> ConversationEvalResult:
        calls.append(f"legacy {unit_id}")
        return ConversationEvalResult(session_id=unit_id)

    real_patched = workflow.patched
    workflow.patched = lambda patch_id: False if patch_id == GATE else real_patched(patch_id)  # type: ignore[assignment]
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=QUEUE,
                workflows=[SalesEvalWorkflow],
                activities=[units, score, select, evaluate],
            ):
                handle = await env.client.start_workflow(
                    SalesEvalWorkflow.run,
                    EvalWindowInput(lookback_hours=24),
                    id="sales-eval-prebands",
                    task_queue=QUEUE,
                )
                await handle.result()
                history = await handle.fetch_history()
    finally:
        workflow.patched = real_patched  # type: ignore[assignment]
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    return raw, calls


async def _main(out_dir: Path) -> None:
    from tests.fixtures.generate_sales_v2_burst_prepatch_fixture import patch_markers

    raw, calls = await _generate()
    assert GATE not in patch_markers(raw), "la history pre-patch trae el marcador"
    assert calls == [
        "scorecard wa_100000000001",
        "scorecard wa_100000000002",
        "legacy wa_100000000001::ep_001",
    ], f"forma inesperada: {calls}"
    events = json.loads(raw)["events"]
    assert not any("activityTaskFailedEventAttributes" in e for e in events), "una activity falló en la fixture"
    (out_dir / FIXTURE).write_text(raw, encoding="utf-8")
    print(f"escrita {out_dir / FIXTURE}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
