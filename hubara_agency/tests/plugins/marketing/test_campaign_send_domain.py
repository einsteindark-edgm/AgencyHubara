"""Envío por lotes (V2) — reglas puras que se re-evalúan EN cada lote.

El plan se arma al disparar, pero una campaña de miles de contactos tarda:
entre el plan y el lote N un cliente puede darse de baja, pasar a manos de un
humano o entrar en su horario de silencio. Cada lote vuelve a mirar el
metadata del contacto y la hora de ahora antes de enviarle.
"""
from src.plugins.marketing.domain.campaign_send import recheck_batch
from src.plugins.marketing.domain.campaigns import RecipientSendPlan, SkippedRecipient
from src.sdk.messagingkit import mark_marketing_opt_out


def _recipient(session_id: str) -> RecipientSendPlan:
    return RecipientSendPlan(session_id=session_id, variables={"greeting": "Hola"})


def _never_quiet(_session_id: str) -> bool:
    return False


def test_a_contact_who_opted_out_after_the_plan_is_skipped_with_the_opt_out_record() -> None:
    opted_out = mark_marketing_opt_out({}, now_ms=5_000, source="texto", campaign_id="mkt-0")

    batch = recheck_batch(
        [_recipient("wa_1"), _recipient("wa_2")],
        {"wa_1": {"tag": "INTERESADO"}, "wa_2": opted_out},
        campaign_id="mkt-1",
        is_quiet_hours=_never_quiet,
    )

    assert batch.recipients == [_recipient("wa_1")]
    assert batch.skipped == [
        SkippedRecipient(
            "wa_2",
            "dado_de_baja",
            opted_out_at_ms=5_000,
            opted_out_source="texto",
            opted_out_campaign_id="mkt-0",
        )
    ]


def test_a_contact_handed_to_a_human_after_the_plan_is_skipped() -> None:
    batch = recheck_batch(
        [_recipient("wa_1")],
        {"wa_1": {"tag": "INTERESADO", "active_route": "humano"}},
        campaign_id="mkt-1",
        is_quiet_hours=_never_quiet,
    )

    assert batch.recipients == []
    assert batch.skipped == [SkippedRecipient("wa_1", "excluido")]


def test_a_contact_now_in_quiet_hours_is_skipped() -> None:
    batch = recheck_batch(
        [_recipient("wa_1"), _recipient("wa_2")],
        {"wa_1": {}, "wa_2": {}},
        campaign_id="mkt-1",
        is_quiet_hours=lambda session_id: session_id == "wa_2",
    )

    assert batch.recipients == [_recipient("wa_1")]
    assert batch.skipped == [SkippedRecipient("wa_2", "quiet_hours")]


def test_an_imported_contact_without_conversation_still_receives_it() -> None:
    """Contacto del CSV sin sesión en el vault: no hay metadata que lo frene."""
    batch = recheck_batch(
        [_recipient("wa_9")], {}, campaign_id="mkt-1", is_quiet_hours=_never_quiet
    )

    assert batch.recipients == [_recipient("wa_9")]
    assert batch.skipped == []


def test_a_contact_who_already_got_this_campaign_is_not_sent_it_again() -> None:
    """Un lote que se vuelve a correr (un Reset desde la UI de Temporal, un
    lote relanzado) no le reenvía la campaña a quien ya la recibió: el send de
    plataforma solo deduplica durante 2 minutos. Una prueba del operador no
    cuenta como recibida."""
    batch = recheck_batch(
        [_recipient("wa_1"), _recipient("wa_2"), _recipient("wa_3")],
        {
            "wa_1": {"campaign_touches": [{"campaign_id": "mkt-1", "sent_at_ms": 1}]},
            "wa_2": {"campaign_touches": [{"campaign_id": "mkt-1", "test": True}]},
            "wa_3": {"campaign_touches": [{"campaign_id": "mkt-0", "sent_at_ms": 1}]},
        },
        campaign_id="mkt-1",
        is_quiet_hours=_never_quiet,
    )

    assert batch.recipients == [_recipient("wa_2"), _recipient("wa_3")]
    assert batch.skipped == [SkippedRecipient("wa_1", "ya_recibida")]


def test_the_plan_remembers_who_may_skip_the_48h_rest() -> None:
    """Agregado a mano o importado: el operador lo eligió a conciencia y se
    salta el respiro entre campañas. El lote lo vuelve a mirar al enviar y
    necesita saberlo."""
    from src.plugins.marketing.domain.campaigns import build_send_plan, new_campaign

    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["segments"] = ["clientes"]
    campaign["extra_session_ids"] = ["wa_2"]
    campaign["imported_contacts"] = [{"phone": "573009990000", "name": "Ana"}]

    class _Card:
        rates: dict = {}

    plan = build_send_plan(
        campaign,
        [("wa_1", {"tag": "COMPRA_EXITOSA"}), ("wa_2", {})],
        _Card(),
        now_ms=10,
    )

    assert {r.session_id: r.bypass_cooldown for r in plan.recipients} == {
        "wa_1": False,
        "wa_2": True,
        "wa_573009990000": True,
    }


def test_a_contact_who_got_another_campaign_meanwhile_gets_the_48h_rest() -> None:
    """Dos campañas que se cruzan (una pausada, otra planeada mientras tanto):
    el respiro se vuelve a mirar al enviar, contra OTRAS campañas."""
    now = 100 * 3_600_000
    recent_other = {"campaign_touches": [{"campaign_id": "mkt-0", "sent_at_ms": now - 3_600_000}]}
    own_test = {"campaign_touches": [{"campaign_id": "mkt-1", "test": True, "sent_at_ms": now}]}
    chosen = RecipientSendPlan(session_id="wa_3", variables={}, bypass_cooldown=True)

    batch = recheck_batch(
        [_recipient("wa_1"), _recipient("wa_2"), chosen],
        {"wa_1": recent_other, "wa_2": own_test, "wa_3": recent_other},
        campaign_id="mkt-1",
        now_ms=now,
        is_quiet_hours=_never_quiet,
    )

    assert [r.session_id for r in batch.recipients] == ["wa_2", "wa_3"]
    assert batch.skipped == [SkippedRecipient("wa_1", "campana_reciente")]
