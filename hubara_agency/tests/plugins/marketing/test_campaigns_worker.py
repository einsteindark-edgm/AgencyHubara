"""Worker `campaigns` — el cableado real del envío por lotes.

Los workers que arma el módulo (campañas + envíos con su ritmo hacia Meta),
con las activities reales sobre un vault aislado: solo el POST a Meta es
fake. Si falta registrar algo (el V2, el lote, una activity, el queue de
envíos), la campaña no sale y esto lo dice.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment

from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaign_send import CampaignSendInput, sends_task_queue
from src.plugins.marketing.domain.campaigns import new_campaign
from src.sdk import get_task_queue
from src.sdk.messagingkit import CarouselCard

QUEUE = get_task_queue("marketing", "campaigns")


def _seed(vault: Path) -> None:
    campaign = new_campaign(campaign_id="mkt-1", name="Promo madre", now_ms=1_000)
    campaign["segments"] = ["clientes"]
    campaign["message"]["body"] = "Velas artesanales hasta el viernes."
    CampaignStore(vault).save(campaign)
    for n in (1, 2, 3):
        session_dir = vault / f"wa_57300000000{n}"
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text(
            json.dumps({"tag": "COMPRA_EXITOSA"}), encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_sends_go_through_their_own_queue_with_a_pace_toward_meta(
    temporal_env: WorkflowEnvironment,
) -> None:
    import src.plugins.marketing.workers.campaigns as worker_mod

    workers = {w.task_queue: w for w in worker_mod.build_workers(temporal_env.client)}

    assert set(workers) == {QUEUE, sends_task_queue(QUEUE)}
    pace = workers[sends_task_queue(QUEUE)].config().get("max_task_queue_activities_per_second")
    assert pace == worker_mod.CAMPAIGN_SENDS_PER_SECOND
    assert 0 < pace <= 80  # Meta: 80 mensajes/s por número, por defecto


@pytest.mark.asyncio
async def test_the_worker_sends_a_campaign_end_to_end_over_the_vault(
    temporal_env: WorkflowEnvironment, _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts
    import src.plugins.marketing.workers.campaigns as worker_mod

    sent: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def fake_send(
        session_id: str,
        template_name: str,
        variables: dict,
        carousel_cards: list[CarouselCard] | None = None,
    ) -> dict:
        sent.append(session_id)
        return {"wa_message_id": f"wamid-{session_id}", "ok": True, "error": None}

    monkeypatch.setattr(worker_mod, "send_whatsapp_template_activity", fake_send)
    monkeypatch.setattr(acts, "is_quiet_hours_for_session", lambda session_id, now_utc: False)
    vault = _isolate_vault_dir
    _seed(vault)

    main, sends = worker_mod.build_workers(temporal_env.client)
    async with main, sends:
        summary = await temporal_env.client.execute_workflow(
            "CampaignSendWorkflowV2",
            CampaignSendInput(campaign_id="mkt-1", campaign_name="Promo madre"),
            id="campaign-send-mkt-1",
            task_queue=QUEUE,
        )

    assert summary["sent"] == 3
    assert sorted(sent) == [f"wa_57300000000{n}" for n in (1, 2, 3)]
    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "sent"
    assert saved["send_result"]["sent"] == 3
    assert saved["progress"]["processed"] == 3
    touches = json.loads((vault / "wa_573000000001" / "metadata.json").read_text())["campaign_touches"]
    assert [(t["campaign_id"], t["wa_message_id"]) for t in touches] == [
        ("mkt-1", "wamid-wa_573000000001")
    ]
