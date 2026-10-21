"""Activities del envío de campañas — ActivityEnvironment + vault aislado."""
import asyncio
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
    _seed_session(vault, "wa_+578", {"tag": "INTERESADO"})  # fuera de segmento
    _seed_session(vault, "wa_+577", {"tag": "COMPRA_EXITOSA", "seeded_test": True})

    start = await ActivityEnvironment().run(prepare_campaign_send_activity, "mkt-1")

    assert (start.total, start.template_name, start.carousel, start.blocked_reason) == (
        3,
        "campaign_promo_marketing_v1",
        False,
        None,
    )
    plan = _send_store(vault).plan("mkt-1")
    assert sorted(r.session_id for r in plan.recipients) == ["wa_+571", "wa_+572", "wa_+573"]
    # Los «fuera de segmento» son el resto del vault: no son de esta campaña
    # (inflaban el resultado y cada GET /campaigns).
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
async def test_finish_removes_the_plan_and_a_retried_finish_keeps_the_result(
    _isolate_vault_dir: Path,
) -> None:
    """El plan (nombres y números de miles de contactos) no se queda en el
    vault después del cierre: el resultado ya está en la campaña. Si la
    activity se reintenta después de borrarlo, no pisa ese resultado."""
    from src.plugins.marketing.agent.campaigns.activities import finish_campaign_send_activity
    from src.plugins.marketing.domain.campaign_send import CampaignSendProgress

    vault = _isolate_vault_dir
    _seed_campaign(vault, status="sending")
    await _sent_two_batches(vault)
    progress = CampaignSendProgress(total=300, processed=200, sent=197, failed=1, opted_out=1, skipped=1)

    await ActivityEnvironment().run(finish_campaign_send_activity, "mkt-1", progress, "cancelled", None)
    first = CampaignStore(vault).get("mkt-1")
    await ActivityEnvironment().run(finish_campaign_send_activity, "mkt-1", progress, "cancelled", None)

    assert not (vault / "_campaigns" / "_sends" / "mkt-1").exists()
    assert CampaignStore(vault).get("mkt-1")["send_result"] == first["send_result"]
    assert first["send_result"]["sent"] == 197


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
async def test_a_cancellation_keeps_its_reason(_isolate_vault_dir: Path) -> None:
    """Una pausa olvidada que se canceló sola, o una cancelación desde
    Temporal: el operador ve por qué."""
    from src.plugins.marketing.agent.campaigns.activities import finish_campaign_send_activity
    from src.plugins.marketing.domain.campaign_send import CampaignSendProgress

    vault = _isolate_vault_dir
    _seed_campaign(vault, status="paused")
    await _sent_two_batches(vault)
    reason = "Estuvo pausada más de 7 días sin reanudarse: se canceló sola."

    await ActivityEnvironment().run(
        finish_campaign_send_activity,
        "mkt-1",
        CampaignSendProgress(total=300, processed=200, sent=197),
        "cancelled",
        reason,
    )

    saved = CampaignStore(vault).get("mkt-1")
    assert (saved["status"], saved["failure_reason"]) == ("cancelled", reason)


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
    # No salió nada: no tiene fecha de envío.
    assert saved["sent_at_ms"] is None


async def _ticks_while(awaitable) -> int:
    """Cuántas veces respira el event loop mientras corre `awaitable`."""
    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        await awaitable
    finally:
        ticker.cancel()
    return ticks


@pytest.mark.asyncio
async def test_preparing_the_plan_does_not_freeze_the_worker(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    """El worker de campañas corre los envíos a Meta en el mismo proceso: un
    escaneo largo del vault no puede congelar su event loop (ni el heartbeat)."""
    import time

    import src.plugins.marketing.agent.campaigns.activities as acts

    class _SlowVault:
        def __init__(self, *_args) -> None:
            pass

        def scan_sessions(self) -> list:
            time.sleep(1.0)
            return []

    _seed_campaign(_isolate_vault_dir)
    monkeypatch.setattr(acts, "FilesystemAttributionStore", _SlowVault)

    ticks = await _ticks_while(
        ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")
    )

    assert ticks >= 5


@pytest.mark.asyncio
async def test_loading_a_batch_does_not_freeze_the_worker(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import time

    import src.plugins.marketing.agent.campaigns.activities as acts
    from src.plugins.marketing.campaign_send_store import CampaignSendStore

    def _slow_recipients(self, campaign_id: str, start: int, end: int) -> list:
        time.sleep(1.0)
        return []

    monkeypatch.setattr(CampaignSendStore, "recipients", _slow_recipients)

    ticks = await _ticks_while(
        ActivityEnvironment().run(acts.load_campaign_batch_activity, "mkt-1", 0, 100)
    )

    assert ticks >= 5


# --- Entrega de UN mensaje (envío + touch en una sola activity) ---------------

_SESSION = "wa_573001112233"


def _delivery(session_id: str = _SESSION):
    from src.plugins.marketing.domain.campaign_send import CampaignDelivery

    return CampaignDelivery(
        campaign_id="mkt-1",
        session_id=session_id,
        template_name="campaign_promo_marketing_v1",
        variables={"greeting": "Hola"},
        touch={
            "campaign_id": "mkt-1",
            "campaign_name": "Promo madre",
            "message": "15% en velas artesanales.",
            "coupon_code": "MAMA15",
            "product_handles": [],
        },
    )


def _metadata(vault: Path, session_id: str = _SESSION) -> dict:
    path = vault / session_id / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.fixture()
def meta_sends(monkeypatch) -> list[str]:
    """Meta en memoria: cada envío devuelve un wamid nuevo (jamás la real)."""
    from types import SimpleNamespace

    import src.plugins.marketing.agent.campaigns.activities as acts

    calls: list[str] = []

    async def _send(session_id, template_name, variables, **kw):
        calls.append(session_id)
        return SimpleNamespace(wa_message_id=f"wamid-{len(calls)}", ok=True, error=None)

    monkeypatch.setattr(acts, "send_template_to_session", _send)
    return calls


@pytest.mark.asyncio
async def test_a_delivery_sends_and_stamps_the_touch_in_one_step(
    _isolate_vault_dir: Path, meta_sends: list[str]
) -> None:
    from src.plugins.marketing.agent.campaigns.activities import deliver_campaign_message_activity

    outcome = await ActivityEnvironment().run(deliver_campaign_message_activity, _delivery())

    assert (outcome.outcome, outcome.wa_message_id, outcome.stamped) == ("sent", "wamid-1", True)
    assert meta_sends == [_SESSION]
    [touch] = _metadata(_isolate_vault_dir)["campaign_touches"]
    assert touch["campaign_id"] == "mkt-1"
    assert touch["coupon_code"] == "MAMA15"
    assert touch["wa_message_id"] == "wamid-1"
    assert isinstance(touch["sent_at_ms"], int)


@pytest.mark.asyncio
async def test_a_retried_delivery_does_not_send_it_twice(
    _isolate_vault_dir: Path, meta_sends: list[str]
) -> None:
    """El worker murió después de enviar y anotar (un deploy): el reintento ve
    el touch y no reenvía, aunque hayan pasado más de los 2 min de la dedup
    del send de plataforma."""
    from src.plugins.marketing.agent.campaigns.activities import deliver_campaign_message_activity

    _seed_session(
        _isolate_vault_dir,
        _SESSION,
        {"campaign_touches": [{"campaign_id": "mkt-1", "sent_at_ms": 1, "wa_message_id": "wamid-0"}]},
    )

    outcome = await ActivityEnvironment().run(deliver_campaign_message_activity, _delivery())

    assert (outcome.outcome, outcome.wa_message_id) == ("sent", "wamid-0")
    assert meta_sends == []


@pytest.mark.asyncio
async def test_a_touch_that_cannot_be_written_still_counts_as_sent(
    _isolate_vault_dir: Path, meta_sends: list[str], monkeypatch
) -> None:
    """Lo que ya salió no se reintenta por no poder anotarlo (eso lo
    reenviaría): sale como enviado, marcado sin touch."""
    import src.plugins.marketing.agent.campaigns.activities as acts

    def _broken_update(self, session_id, mutator):
        raise OSError("disco lleno")

    monkeypatch.setattr(acts.FilesystemMetadataStore, "update", _broken_update)

    outcome = await ActivityEnvironment().run(acts.deliver_campaign_message_activity, _delivery())

    assert (outcome.outcome, outcome.stamped) == ("sent", False)
    assert meta_sends == [_SESSION]


@pytest.mark.asyncio
async def test_meta_131050_marks_the_opt_out_with_this_campaign(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts

    async def _rejected(session_id, template_name, variables, **kw):
        raise ApplicationError(
            "WhatsApp template send failed (non-retryable, code=131050)",
            non_retryable=True,
            type="TemplateMetaError131050",
        )

    monkeypatch.setattr(acts, "send_template_to_session", _rejected)

    outcome = await ActivityEnvironment().run(acts.deliver_campaign_message_activity, _delivery())

    assert outcome.outcome == "opted_out"
    metadata = _metadata(_isolate_vault_dir)
    assert metadata["marketing_opt_out"] is True
    assert metadata["marketing_opt_out_campaign_id"] == "mkt-1"


@pytest.mark.asyncio
async def test_other_meta_rejections_fail_the_delivery(_isolate_vault_dir: Path, monkeypatch) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts

    async def _rejected(session_id, template_name, variables, **kw):
        raise ApplicationError("plantilla pausada", non_retryable=True, type="TemplateMetaError132012")

    monkeypatch.setattr(acts, "send_template_to_session", _rejected)

    with pytest.raises(ApplicationError) as err:
        await ActivityEnvironment().run(acts.deliver_campaign_message_activity, _delivery())

    assert err.value.type == "TemplateMetaError132012"
    assert "campaign_touches" not in _metadata(_isolate_vault_dir)


@pytest.mark.asyncio
async def test_at_send_time_an_opt_out_or_quiet_hours_skip_without_sending(
    _isolate_vault_dir: Path, meta_sends: list[str], monkeypatch
) -> None:
    """El envío puede esperar turno en la cola (ritmo hacia Meta, un worker
    caído): se re-chequea al momento de mandar, no solo al cargar el lote."""
    import src.plugins.marketing.agent.campaigns.activities as acts

    _seed_session(_isolate_vault_dir, "wa_1", {"marketing_opt_out": True})
    monkeypatch.setattr(
        acts, "is_quiet_hours_for_session", lambda session_id, now_utc: session_id == "wa_2"
    )

    opted = await ActivityEnvironment().run(acts.deliver_campaign_message_activity, _delivery("wa_1"))
    quiet = await ActivityEnvironment().run(acts.deliver_campaign_message_activity, _delivery("wa_2"))

    assert (opted.outcome, opted.skip_reason) == ("skipped", "dado_de_baja")
    assert (quiet.outcome, quiet.skip_reason) == ("skipped", "quiet_hours")
    assert meta_sends == []


@pytest.mark.asyncio
async def test_a_batch_carries_what_the_customer_receives_once(_isolate_vault_dir: Path) -> None:
    """El touch de cada envío copia el texto armado una vez por lote: no se
    relee la campaña (que puede pesar cientos de KB) por cada destinatario."""
    import src.plugins.marketing.agent.campaigns.activities as acts

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    await ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")

    batch = await ActivityEnvironment().run(acts.load_campaign_batch_activity, "mkt-1", 0, 1)

    assert batch.touch["campaign_id"] == "mkt-1"
    assert batch.touch["campaign_name"] == "Promo madre"
    assert batch.touch["coupon_code"] == "MAMA15"
    assert "15% en velas" in batch.touch["message"]


@pytest.mark.asyncio
async def test_a_batch_mostly_in_quiet_hours_says_when_the_window_opens(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    """La mayoría del lote fuera de horario: el lote no sale y dice cuándo
    abre la ventana (el envío espera en vez de quemarlos como omitidos)."""
    from datetime import datetime, timedelta, timezone

    import src.plugins.marketing.agent.campaigns.activities as acts

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    for n in (1, 2, 3):
        _seed_session(vault, f"wa_+57{n}", {"tag": "COMPRA_EXITOSA"})
    await ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")
    opens = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(acts, "is_quiet_hours_for_session", lambda session_id, now_utc: now_utc < opens)

    batch = await ActivityEnvironment().run(acts.load_campaign_batch_activity, "mkt-1", 0, 3)

    opens_ms = int(opens.timestamp() * 1000)
    assert batch.quiet_until_ms is not None
    assert opens_ms <= batch.quiet_until_ms <= opens_ms + 15 * 60_000


@pytest.mark.asyncio
async def test_a_few_contacts_in_quiet_hours_do_not_hold_the_batch(
    _isolate_vault_dir: Path, monkeypatch
) -> None:
    import src.plugins.marketing.agent.campaigns.activities as acts

    vault = _isolate_vault_dir
    _seed_campaign(vault)
    for n in (1, 2, 3):
        _seed_session(vault, f"wa_+57{n}", {"tag": "COMPRA_EXITOSA"})
    await ActivityEnvironment().run(acts.prepare_campaign_send_activity, "mkt-1")
    monkeypatch.setattr(
        acts, "is_quiet_hours_for_session", lambda session_id, now_utc: session_id == "wa_+571"
    )

    batch = await ActivityEnvironment().run(acts.load_campaign_batch_activity, "mkt-1", 0, 3)

    assert batch.quiet_until_ms is None
    assert [(s.session_id, s.reason) for s in batch.skipped] == [("wa_+571", "quiet_hours")]
    assert len(batch.recipients) == 2



@pytest.mark.asyncio
async def test_prepare_send_brings_the_interest_profile_audience(_isolate_vault_dir: Path) -> None:
    """El segmento «interesados sin compra» (#451) llega al plan del V2."""
    from src.plugins.marketing.agent.campaigns.activities import prepare_campaign_send_activity
    from src.plugins.marketing.domain.campaigns import SEGMENT_INTERESADOS_SIN_COMPRA

    vault = _isolate_vault_dir
    _seed_campaign(vault, segments=[SEGMENT_INTERESADOS_SIN_COMPRA])
    _seed_session(
        vault,
        "wa_+571",
        {
            "tag": "SIN_RESPUESTA",
            "episodes": [{"episode_id": "ep_001", "started_at_ms": 1_000}],
            "profile_tags": {"interesado_sin_compra": {"at_ms": 5_000, "episode_id": "ep_001"}},
        },
    )
    _seed_session(vault, "wa_+572", {"tag": "SIN_RESPUESTA"})

    start = await ActivityEnvironment().run(prepare_campaign_send_activity, "mkt-1")

    assert start.total == 1
    [recipient] = _send_store(vault).plan("mkt-1").recipients
    assert (recipient.session_id, recipient.profile_segment) == ("wa_+571", SEGMENT_INTERESADOS_SIN_COMPRA)
