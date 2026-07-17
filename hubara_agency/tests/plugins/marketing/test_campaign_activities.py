"""Activities del envío de campañas — ActivityEnvironment + vault aislado."""
import json
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from src.plugins.marketing.agent.campaigns.activities import (
    load_campaign_send_plan_activity,
    mark_campaign_sending_activity,
    record_campaign_send_result_activity,
    stamp_campaign_touch_activity,
)
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaigns import new_campaign


def _seed_session(vault: Path, session_id: str, metadata: dict) -> None:
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )


def _seed_campaign(vault: Path, **overrides) -> dict:
    campaign = new_campaign(
        campaign_id="mkt-1", name="Promo madre", now_ms=1_000
    )
    campaign["segments"] = ["clientes"]
    campaign["percent"] = 15
    campaign["coupon_code"] = "MAMA15"
    campaign["message"]["body"] = "15% en velas artesanales hasta el viernes."
    campaign.update(overrides)
    CampaignStore(vault).save(campaign)
    return campaign


@pytest.fixture(autouse=True)
def _sin_quiet_hours(monkeypatch):
    """Los tests que no apuntan a quiet hours no pueden depender de la hora
    de la máquina que corre pytest — se fija el predicado en False."""
    import src.plugins.marketing.agent.campaigns.activities as acts

    monkeypatch.setattr(
        acts, "is_quiet_hours_for_session", lambda session_id, now_utc: False
    )


@pytest.mark.asyncio
async def test_load_plan_resuelve_audiencia_y_costo(_isolate_vault_dir: Path) -> None:
    vault = _isolate_vault_dir
    _seed_campaign(vault)
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+572", {"tag": "INTERESADO"})
    _seed_session(vault, "wa_+573", {"tag": "HUMANO"})

    plan = await ActivityEnvironment().run(
        load_campaign_send_plan_activity, "mkt-1"
    )

    assert [r.session_id for r in plan.recipients] == ["wa_+571"]
    variables = plan.recipients[0].variables
    assert variables["greeting"] == "Hola"
    assert "MAMA15" in variables["campaign_offer"]
    assert plan.template_name == "campaign_promo_marketing_v1"
    # Tarifa marketing CO vigente (rate card co_2026q2_v1): $0.0125/msg.
    assert plan.unit_cost_usd_micros == 12500
    assert plan.total_cost_usd_micros == 12500
    skipped_reasons = {s.session_id: s.reason for s in plan.skipped}
    assert skipped_reasons["wa_+573"] == "excluido"


@pytest.mark.asyncio
async def test_load_plan_aplica_quiet_hours_y_cadencia(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import time as _time

    import src.plugins.marketing.agent.campaigns.activities as acts

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    now = int(_time.time() * 1000)
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+572", {"tag": "COMPRA_EXITOSA"})  # quiet hours
    _seed_session(
        vault,
        "wa_+573",
        {
            "tag": "COMPRA_EXITOSA",
            "campaign_touches": [
                {"campaign_id": "mkt-otra", "sent_at_ms": now - 3_600_000}
            ],
        },
    )
    monkeypatch.setattr(
        acts,
        "is_quiet_hours_for_session",
        lambda session_id, now_utc: session_id == "wa_+572",
    )

    plan = await ActivityEnvironment().run(
        load_campaign_send_plan_activity, "mkt-1"
    )

    assert [r.session_id for r in plan.recipients] == ["wa_+571"]
    reasons = {s.session_id: s.reason for s in plan.skipped}
    assert reasons["wa_+572"] == "quiet_hours"
    assert reasons["wa_+573"] == "campana_reciente"
    # El costo estimado refleja SOLO lo que se va a enviar.
    assert plan.total_cost_usd_micros == 12500


@pytest.mark.asyncio
async def test_load_plan_campana_inexistente_es_non_retryable(
    _isolate_vault_dir: Path,
) -> None:
    with pytest.raises(ApplicationError) as err:
        await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-nope")
    assert err.value.non_retryable is True


@pytest.mark.asyncio
async def test_stamp_campaign_touch_appendea_y_capea(_isolate_vault_dir: Path) -> None:
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})

    env = ActivityEnvironment()
    await env.run(stamp_campaign_touch_activity, "wa_+571", "mkt-1", "Promo madre")
    await env.run(stamp_campaign_touch_activity, "wa_+571", "mkt-2", "Otra")

    metadata = json.loads((vault / "wa_+571" / "metadata.json").read_text())
    touches = metadata["campaign_touches"]
    assert [t["campaign_id"] for t in touches] == ["mkt-1", "mkt-2"]
    assert touches[0]["campaign_name"] == "Promo madre"
    assert isinstance(touches[0]["sent_at_ms"], int)
    # El tag original sobrevive (merge, no clobber).
    assert metadata["tag"] == "COMPRA_EXITOSA"


@pytest.mark.asyncio
async def test_record_result_deja_la_campana_sent(_isolate_vault_dir: Path) -> None:
    vault = _isolate_vault_dir
    _seed_campaign(vault)
    env = ActivityEnvironment()
    await env.run(mark_campaign_sending_activity, "mkt-1")
    assert CampaignStore(vault).get("mkt-1")["status"] == "sending"

    await env.run(
        record_campaign_send_result_activity,
        "mkt-1",
        {
            "planned": 2,
            "sent": 1,
            "failed": ["wa_+579"],
            "skipped": [{"session_id": "wa_+573", "reason": "excluido"}],
            "unit_cost_usd_micros": 12500,
            "spent_usd_micros": 12500,
        },
    )
    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "sent"
    assert saved["sent_at_ms"] is not None
    assert saved["send_result"]["sent"] == 1
    assert saved["send_result"]["spent_usd_micros"] == 12500
