"""Procedencia de las fixtures de replay del envío de campañas (2026-10-15).

Las fixtures quedan commiteadas; este script es el registro reproducible de
cómo se produjeron. Historias sintéticas (campaña `mkt-1`, sesiones `wa_<n>`
inventadas), activities fake, identidad del worker → `fixture-worker`:

  * `history_campaign_send_v1.json` — `CampaignSendWorkflow` (el V1) tal como
    corre hoy. CONGELADA: las campañas programadas con ese tipo antes del
    deploy del V2 arrancan en el worker nuevo; si alguien toca el V1, esta
    historia deja de reproducirse. Se borra junto con el V1, cuando ya no
    quede ninguna campaña programada con el tipo viejo.
  * `history_campaign_send_v2.json` — `CampaignSendWorkflowV2`: 3
    destinatarios en lotes de 2, con una pausa (Update) durante el primer
    lote y su reanudación.
  * `history_campaign_send_batch_v1.json` / `..._batch_failed_v1.json` — los
    dos `CampaignSendBatchWorkflow` hijos de esa corrida: el primero con un
    envío y una baja de Meta (131050), el segundo con un envío fallido.

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_campaign_send_fixtures.py [out_dir]
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from temporalio.client import WorkflowHandle  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from src.plugins.marketing.agent.campaigns.workflows.send import (  # noqa: E402
    CampaignSendWorkflow,
)
from src.plugins.marketing.agent.campaigns.workflows.send_v2 import (  # noqa: E402
    CampaignSendBatchWorkflow,
    CampaignSendWorkflowV2,
)
from src.plugins.marketing.domain.campaign_send import (  # noqa: E402
    CampaignSendInput,
    sends_task_queue,
)
from src.sdk import get_task_queue  # noqa: E402
from tests.plugins.marketing import test_send_workflow as v1_tests  # noqa: E402
from tests.plugins.marketing import test_send_workflow_v2 as v2_tests  # noqa: E402

QUEUE = get_task_queue("marketing", "campaigns")
_IDENTITY = "fixture-worker"


def _sanitized(raw: str) -> str:
    """Sin datos de la máquina: identidad del worker fija y trazas de los
    fallos vacías (traen rutas locales; el replay no las usa)."""
    raw = re.sub(r'("identity":\s*")[^"]+(")', rf"\1{_IDENTITY}\2", raw)
    return re.sub(r'("stackTrace":\s*")(?:[^"\\]|\\.)*(")', r"\1\2", raw)


async def _dump(handle: WorkflowHandle, path: Path) -> None:
    history = await handle.fetch_history()
    path.write_text(_sanitized(history.to_json()), encoding="utf-8")
    print(f"{path.name}: {len(history.events)} eventos")


async def _v1(env: WorkflowEnvironment, out: Path) -> None:
    tracker = v1_tests.Tracker()
    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[CampaignSendWorkflow],
        activities=v1_tests._fakes(tracker, opted_out_session="wa_b"),
        identity=_IDENTITY,
    ):
        handle = await env.client.start_workflow(
            CampaignSendWorkflow.run,
            args=["mkt-1", "Promo madre"],
            id="campaign-send-mkt-1-v1",
            task_queue=QUEUE,
        )
        await handle.result()
        await _dump(handle, out / "history_campaign_send_v1.json")


async def _v2(env: WorkflowEnvironment, out: Path) -> None:
    fakes = v2_tests.Fakes(
        3, hold_batch_at=0, opted_out=frozenset({"wa_1"}), failing=frozenset({"wa_2"})
    )
    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[CampaignSendWorkflowV2, CampaignSendBatchWorkflow],
        activities=fakes.activities(),
        identity=_IDENTITY,
    ), Worker(
        env.client,
        task_queue=sends_task_queue(QUEUE),
        activities=[fakes.send_activity()],
        identity=_IDENTITY,
    ):
        handle = await env.client.start_workflow(
            CampaignSendWorkflowV2.run,
            CampaignSendInput(campaign_id="mkt-1", campaign_name="Promo madre", batch_size=2),
            id=v2_tests.WORKFLOW_ID,
            task_queue=QUEUE,
        )
        await asyncio.wait_for(fakes.holding.wait(), timeout=30)
        await handle.execute_update(CampaignSendWorkflowV2.pause)
        fakes.release.set()
        await asyncio.wait_for(fakes.paused_recorded.wait(), timeout=30)
        await handle.execute_update(CampaignSendWorkflowV2.resume)
        await handle.result()
        await _dump(handle, out / "history_campaign_send_v2.json")
        batches = [
            ("0000000", "history_campaign_send_batch_v1.json"),
            ("0000002", "history_campaign_send_batch_failed_v1.json"),
        ]
        for start, name in batches:
            child = env.client.get_workflow_handle(f"{v2_tests.WORKFLOW_ID}-batch-{start}")
            await _dump(child, out / name)


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _v1(env, out)
        await _v2(env, out)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent))
