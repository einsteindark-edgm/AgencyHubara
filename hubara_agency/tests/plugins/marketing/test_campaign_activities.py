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


def _promo(code: str = "MAMA15", **over):
    from src.sdk.connectorkit import PromotionDTO

    base = dict(
        id=f"p_{code}", code=code, discount_type="percentage", value=15, currency_code=None,
        target_type="items", allocation="across", max_quantity=None,
        product_ids=(), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
        budget_type=None, budget_limit=None, budget_used=None, description=None,
    )
    return PromotionDTO(**{**base, **over})


@pytest.fixture(autouse=True)
def _cupon_vigente(monkeypatch):
    """El envío re-valida el cupón al dispararse: por defecto MAMA15 rige
    (Medusa en memoria; jamás la real)."""
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import FakePromotionsPort

    port = FakePromotionsPort([_promo()])
    monkeypatch.setattr(acts, "get_promotions_port", lambda: port)
    return port


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


@pytest.mark.asyncio
async def test_stamp_campaign_touch_guarda_lo_que_recibio_el_cliente(
    _isolate_vault_dir: Path,
) -> None:
    """Respuesta a campaña (2026-09-22): el bot necesita saber QUÉ se le mandó
    al cliente — mensaje, cupón y productos viajan en el touch."""
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_573001234567", {"tag": "INTERESADO"})
    _seed_campaign(vault, carousel_handles=["duo-zodiacal", "cruz-de-vida"])

    await ActivityEnvironment().run(
        stamp_campaign_touch_activity, "wa_573001234567", "mkt-1", "Promo madre"
    )

    metadata = json.loads((vault / "wa_573001234567" / "metadata.json").read_text())
    touch = metadata["campaign_touches"][-1]
    assert "15% en velas artesanales" in touch["message"]
    assert "MAMA15" in touch["message"]
    assert touch["coupon_code"] == "MAMA15"
    assert touch["product_handles"] == ["duo-zodiacal", "cruz-de-vida"]
    assert "test" not in touch


@pytest.mark.asyncio
async def test_stamp_campaign_touch_guarda_el_id_del_mensaje(_isolate_vault_dir: Path) -> None:
    """Ads (2026-09-25): el webhook de estados encuentra el touch por el id
    del mensaje y anota ahí si se entregó, si se leyó y cuánto costó."""
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_573001234567", {"tag": "INTERESADO"})
    _seed_campaign(vault)

    await ActivityEnvironment().run(
        stamp_campaign_touch_activity, "wa_573001234567", "mkt-1", "Promo madre", "wamid.CAMP1"
    )

    metadata = json.loads((vault / "wa_573001234567" / "metadata.json").read_text())
    assert metadata["campaign_touches"][-1]["wa_message_id"] == "wamid.CAMP1"


# --- El cupón se re-valida al DISPARAR el envío (premortem A12) ----------------


def _attempt(n: int) -> ActivityEnvironment:
    import dataclasses

    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, attempt=n)
    return env


@pytest.mark.asyncio
async def test_send_time_recheck_copies_the_coupon_terms_of_now(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    """La campaña se programó con 15 % "hasta el 30"; al dispararse el cupón
    dice 20 % hasta el 27: el mensaje y la campaña anuncian lo de AHORA."""
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import FakePromotionsPort

    vault = _isolate_vault_dir
    _seed_campaign(vault, valid_until="30 de septiembre")
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    promo = _promo(value=20, ends_at_ms=1_790_571_600_000)  # 2026-09-28T05:00Z → "27 de septiembre"
    monkeypatch.setattr(acts, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    # El envío se dispara antes de que el cupón venza (el reloj real ya pasó el 27).
    from datetime import datetime as _real_datetime

    class _BeforeTheEnd(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _real_datetime.fromtimestamp((1_790_571_600_000 - 3 * 86_400_000) / 1000, tz=tz)

    monkeypatch.setattr(acts, "datetime", _BeforeTheEnd)

    plan = await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-1")

    assert plan.blocked_reason is None
    assert "27 de septiembre" in plan.recipients[0].variables["campaign_offer"]
    saved = CampaignStore(vault).get("mkt-1")
    assert (saved["percent"], saved["valid_until"]) == (20, "27 de septiembre")


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"status": "inactive"}, "pausado"),
        ({"ends_at_ms": 1_000}, "venció"),
        ({"code": "OTRO"}, "no existe"),
    ],
)
@pytest.mark.asyncio
async def test_send_time_recheck_blocks_the_send_with_a_clear_reason(
    _isolate_vault_dir: Path, monkeypatch, over, fragment
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import FakePromotionsPort

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    monkeypatch.setattr(acts, "get_promotions_port", lambda: FakePromotionsPort([_promo(**over)]))

    plan = await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-1")

    assert plan.recipients == []
    assert "MAMA15" in plan.blocked_reason and fragment in plan.blocked_reason
    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "failed"
    assert saved["failure_reason"] == plan.blocked_reason


@pytest.mark.asyncio
async def test_medusa_down_at_send_time_retries_and_on_the_last_attempt_blocks(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import PromotionsUnavailableError

    class Down:
        async def list_active(self):
            raise PromotionsUnavailableError("timeout")

        async def get_by_code(self, code):
            raise PromotionsUnavailableError("timeout")

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    monkeypatch.setattr(acts, "get_promotions_port", lambda: Down())

    with pytest.raises(ApplicationError) as first:
        await _attempt(1).run(load_campaign_send_plan_activity, "mkt-1")
    assert first.value.non_retryable is False  # Temporal reintenta
    assert CampaignStore(vault).get("mkt-1")["status"] == "draft"

    plan = await _attempt(acts.PLAN_MAX_ATTEMPTS).run(load_campaign_send_plan_activity, "mkt-1")

    assert "no respondió" in plan.blocked_reason
    assert CampaignStore(vault).get("mkt-1")["status"] == "failed"


@pytest.mark.asyncio
async def test_a_worker_without_medusa_blocks_instead_of_calling_the_coupon_unknown(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import NullPromotionsPort

    _seed_campaign(_isolate_vault_dir)
    monkeypatch.setattr(acts, "get_promotions_port", lambda: NullPromotionsPort())

    plan = await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-1")

    assert "Medusa" in plan.blocked_reason and "no existe" not in plan.blocked_reason


@pytest.mark.asyncio
async def test_campaign_without_coupon_does_not_ask_medusa(
    _isolate_vault_dir: Path, _cupon_vigente
) -> None:
    vault = _isolate_vault_dir
    _seed_campaign(vault, coupon_code="")
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})

    plan = await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-1")

    assert [r.session_id for r in plan.recipients] == ["wa_+571"]
    assert _cupon_vigente.calls == []


@pytest.mark.asyncio
async def test_send_time_recheck_reads_medusa_fresh_not_the_process_cache(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    """El worker de campañas reusa el `PromotionsPort` (cache de 60 s): un
    cupón pausado justo antes del disparo no puede colarse por el cache."""
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.platform.promotions.medusa import MedusaPromotionsPort

    class Medusa:
        status = "active"

        async def list_promotions(self):
            return [{
                "id": "p_MAMA15", "code": "MAMA15", "status": self.status,
                "application_method": {"type": "percentage", "value": 15, "target_type": "items"},
            }]

        async def list_product_tags(self, ids):
            return []

    medusa = Medusa()
    port = MedusaPromotionsPort(medusa, ttl_s=3600)
    assert await port.get_by_code("MAMA15") is not None  # cache caliente: "activo"
    medusa.status = "inactive"  # el operador lo pausa
    monkeypatch.setattr(acts, "get_promotions_port", lambda: port)
    _seed_campaign(_isolate_vault_dir)

    plan = await ActivityEnvironment().run(load_campaign_send_plan_activity, "mkt-1")

    assert plan.blocked_reason is not None and "pausado" in plan.blocked_reason


# --- Envío por lotes (CampaignSendWorkflowV2) --------------------------------


def _send_store(vault: Path):
    from src.plugins.marketing.campaign_send_store import CampaignSendStore

    return CampaignSendStore(vault)


@pytest.mark.asyncio
async def test_prepare_send_keeps_the_plan_in_the_vault_and_returns_only_the_count(
    _isolate_vault_dir: Path,
) -> None:
    """El plan de miles de contactos no viaja por Temporal (2 MB): queda en
    el vault y el workflow recibe el conteo."""
    from src.plugins.marketing.agent.campaigns.activities import (
        prepare_campaign_send_activity,
    )

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    for n in (1, 2, 3):
        _seed_session(vault, f"wa_+57{n}", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+579", {"tag": "HUMANO"})

    start = await ActivityEnvironment().run(prepare_campaign_send_activity, "mkt-1")

    assert (start.total, start.template_name, start.carousel, start.blocked_reason) == (
        3,
        "campaign_promo_marketing_v1",
        False,
        None,
    )
    plan = _send_store(vault).plan("mkt-1")
    assert sorted(r.session_id for r in plan.recipients) == ["wa_+571", "wa_+572", "wa_+573"]
    assert [(s.session_id, s.reason) for s in plan.skipped] == [("wa_+579", "excluido")]
    assert plan.unit_cost_usd_micros == 12500


@pytest.mark.asyncio
async def test_prepare_send_with_a_paused_coupon_comes_back_blocked(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.sdk.connectorkit import FakePromotionsPort

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    monkeypatch.setattr(
        acts, "get_promotions_port", lambda: FakePromotionsPort([_promo(status="inactive")])
    )

    start = await ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")

    assert start.total == 0
    assert "pausado" in start.blocked_reason
    assert CampaignStore(vault).get("mkt-1")["status"] == "failed"


@pytest.mark.asyncio
async def test_load_batch_rechecks_opt_outs_and_quiet_hours_at_send_time(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    for n in (1, 2, 3, 4):
        _seed_session(vault, f"wa_+57{n}", {"tag": "COMPRA_EXITOSA"})
    await ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")
    # Entre el plan y el lote: uno se da de baja, otro entra en su horario de
    # silencio.
    _seed_session(
        vault,
        "wa_+572",
        {"tag": "COMPRA_EXITOSA", "marketing_opt_out": True, "marketing_opt_out_source": "texto"},
    )
    monkeypatch.setattr(
        acts, "is_quiet_hours_for_session", lambda session_id, now_utc: session_id == "wa_+573"
    )
    plan_ids = [r.session_id for r in _send_store(vault).plan("mkt-1").recipients]

    batch = await ActivityEnvironment().run(acts.load_campaign_batch_activity, "mkt-1", 0, 3)

    first_three = plan_ids[:3]
    expected_skips = {"wa_+572": "dado_de_baja", "wa_+573": "quiet_hours"}
    assert [r.session_id for r in batch.recipients] == [
        s for s in first_three if s not in expected_skips
    ]
    assert {s.session_id: s.reason for s in batch.skipped} == {
        s: expected_skips[s] for s in first_three if s in expected_skips
    }


@pytest.mark.asyncio
async def test_record_progress_shows_the_advance_and_keeps_the_batch(
    _isolate_vault_dir: Path,
) -> None:
    from src.plugins.marketing.agent.campaigns.activities import (
        record_campaign_progress_activity,
    )
    from src.plugins.marketing.domain.campaign_send import (
        CampaignBatchResult,
        CampaignSendProgress,
    )

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    batch = CampaignBatchResult(start=0, end=100, sent=97, failed=["wa_5"], opted_out=["wa_6"])
    progress = CampaignSendProgress(total=300, processed=100, sent=97, failed=1, opted_out=1, skipped=1)

    await ActivityEnvironment().run(record_campaign_progress_activity, "mkt-1", progress, batch)
    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "sending"
    assert saved["progress"]["processed"] == 100
    assert saved["progress"]["total"] == 300
    assert _send_store(vault).batches("mkt-1") == [batch]

    await ActivityEnvironment().run(
        record_campaign_progress_activity,
        "mkt-1",
        CampaignSendProgress(total=300, processed=100, state="paused"),
        None,
    )
    assert CampaignStore(vault).get("mkt-1")["status"] == "paused"


async def _sent_two_batches(vault: Path) -> None:
    from src.plugins.marketing.domain.campaign_send import CampaignBatchResult
    from src.plugins.marketing.domain.campaigns import (
        CampaignSendPlan,
        RecipientSendPlan,
        SkippedRecipient,
    )

    store = _send_store(vault)
    store.save_plan(
        CampaignSendPlan(
            campaign_id="mkt-1",
            template_name="campaign_promo_marketing_v1",
            recipients=[RecipientSendPlan(f"wa_{i}", {}) for i in range(300)],
            skipped=[SkippedRecipient("wa_z", "excluido")],
            unit_cost_usd_micros=12_500,
            total_cost_usd_micros=12_500 * 300,
        )
    )
    store.save_batch(
        "mkt-1",
        CampaignBatchResult(
            start=0, end=100, sent=97, failed=["wa_5"], opted_out=["wa_6"],
            skipped=[SkippedRecipient("wa_7", "quiet_hours")],
        ),
    )
    store.save_batch("mkt-1", CampaignBatchResult(start=100, end=200, sent=100))


@pytest.mark.asyncio
async def test_finish_writes_the_whole_result_from_the_batches(_isolate_vault_dir: Path) -> None:
    from src.plugins.marketing.agent.campaigns.activities import finish_campaign_send_activity
    from src.plugins.marketing.domain.campaign_send import CampaignSendProgress

    vault = _isolate_vault_dir
    _seed_campaign(vault, status="sending")
    await _sent_two_batches(vault)
    progress = CampaignSendProgress(total=300, processed=200, sent=197, failed=1, opted_out=1, skipped=1)

    await ActivityEnvironment().run(finish_campaign_send_activity, "mkt-1", progress, "cancelled", None)

    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "cancelled"
    assert saved["sent_at_ms"] is not None
    assert saved["send_result"] == {
        "planned": 300,
        "processed": 200,
        "sent": 197,
        "failed": ["wa_5"],
        "opted_out": ["wa_6"],
        "skipped": [
            {"session_id": "wa_z", "reason": "excluido"},
            {"session_id": "wa_7", "reason": "quiet_hours"},
        ],
        "unit_cost_usd_micros": 12_500,
        "spent_usd_micros": 12_500 * 197,
    }
    assert saved["progress"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_finish_after_a_failure_leaves_the_campaign_failed_with_the_reason(
    _isolate_vault_dir: Path,
) -> None:
    from src.plugins.marketing.agent.campaigns.activities import finish_campaign_send_activity
    from src.plugins.marketing.domain.campaign_send import CampaignSendProgress

    vault = _isolate_vault_dir
    _seed_campaign(vault, status="sending")
    await _sent_two_batches(vault)
    reason = "El envío se detuvo con 200 de 300 destinatarios procesados: el vault no responde"

    await ActivityEnvironment().run(
        finish_campaign_send_activity,
        "mkt-1",
        CampaignSendProgress(total=300, processed=200, sent=197),
        "failed",
        reason,
    )

    saved = CampaignStore(vault).get("mkt-1")
    assert saved["status"] == "failed"
    assert saved["failure_reason"] == reason
    assert saved["send_result"]["sent"] == 197


@pytest.mark.asyncio
async def test_finish_without_a_plan_still_closes_the_campaign(_isolate_vault_dir: Path) -> None:
    """El plan nunca se guardó (falló antes de armarlo): igual queda fallida
    con el motivo, sin reventar por el plan que no está."""
    from src.plugins.marketing.agent.campaigns.activities import finish_campaign_send_activity
    from src.plugins.marketing.domain.campaign_send import CampaignSendProgress

    vault = _isolate_vault_dir
    _seed_campaign(vault)

    await ActivityEnvironment().run(
        finish_campaign_send_activity, "mkt-1", CampaignSendProgress(total=0), "failed", "Medusa caída"
    )

    saved = CampaignStore(vault).get("mkt-1")
    assert (saved["status"], saved["failure_reason"]) == ("failed", "Medusa caída")
    assert saved["send_result"]["planned"] == 0
