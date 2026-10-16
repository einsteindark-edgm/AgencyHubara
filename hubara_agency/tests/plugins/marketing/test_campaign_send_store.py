"""CampaignSendStore — el plan de un envío y lo que pasó en cada lote, en el
vault (`<vault>/_campaigns/_sends/<id>/`).

El plan de una campaña de miles de contactos no cabe en un payload de
Temporal (2 MB): vive en el vault y el workflow solo lleva un cursor. Cada
lote deja su resultado; el resumen final se arma leyéndolos.
"""
from src.plugins.marketing.campaign_send_store import CampaignSendStore
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaign_send import CampaignBatchResult
from src.plugins.marketing.domain.campaigns import (
    CampaignSendPlan,
    RecipientSendPlan,
    SkippedRecipient,
    new_campaign,
)


def _plan(n: int) -> CampaignSendPlan:
    return CampaignSendPlan(
        campaign_id="mkt-1",
        template_name="campaign_promo_marketing_v1",
        recipients=[
            RecipientSendPlan(session_id=f"wa_{i}", variables={"greeting": f"Hola {i}"})
            for i in range(n)
        ],
        skipped=[SkippedRecipient("wa_z", "excluido")],
        unit_cost_usd_micros=12_500,
        total_cost_usd_micros=12_500 * n,
    )


def test_a_slice_of_the_saved_plan_comes_back_in_order(tmp_path) -> None:
    store = CampaignSendStore(tmp_path)
    store.save_plan(_plan(250))

    recipients = store.recipients("mkt-1", 100, 200)

    assert [r.session_id for r in recipients] == [f"wa_{i}" for i in range(100, 200)]
    assert recipients[0].variables == {"greeting": "Hola 100"}


def test_the_plan_keeps_its_skipped_and_cost(tmp_path) -> None:
    store = CampaignSendStore(tmp_path)
    store.save_plan(_plan(3))

    plan = store.plan("mkt-1")

    assert plan.skipped == [SkippedRecipient("wa_z", "excluido")]
    assert plan.unit_cost_usd_micros == 12_500
    assert len(plan.recipients) == 3


def test_saving_the_same_batch_twice_keeps_one_result(tmp_path) -> None:
    """Un lote que se reintenta (activity, worker caído) no cuenta doble."""
    store = CampaignSendStore(tmp_path)
    second = CampaignBatchResult(start=100, end=200, sent=99, failed=["wa_150"])
    first = CampaignBatchResult(
        start=0, end=100, sent=98, opted_out=["wa_3"], skipped=[SkippedRecipient("wa_7", "quiet_hours")]
    )

    store.save_batch("mkt-1", second)
    store.save_batch("mkt-1", first)
    store.save_batch("mkt-1", second)

    assert store.batches("mkt-1") == [first, second]


def test_the_send_files_are_not_listed_as_campaigns(tmp_path) -> None:
    CampaignStore(tmp_path).save(new_campaign(campaign_id="mkt-1", name="A", now_ms=1))
    store = CampaignSendStore(tmp_path)
    store.save_plan(_plan(2))
    store.save_batch("mkt-1", CampaignBatchResult(start=0, end=2, sent=2))

    assert [c["id"] for c in CampaignStore(tmp_path).list_campaigns()] == ["mkt-1"]
