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
        is_quiet_hours=_never_quiet,
    )

    assert batch.recipients == []
    assert batch.skipped == [SkippedRecipient("wa_1", "excluido")]


def test_a_contact_now_in_quiet_hours_is_skipped() -> None:
    batch = recheck_batch(
        [_recipient("wa_1"), _recipient("wa_2")],
        {"wa_1": {}, "wa_2": {}},
        is_quiet_hours=lambda session_id: session_id == "wa_2",
    )

    assert batch.recipients == [_recipient("wa_1")]
    assert batch.skipped == [SkippedRecipient("wa_2", "quiet_hours")]


def test_an_imported_contact_without_conversation_still_receives_it() -> None:
    """Contacto del CSV sin sesión en el vault: no hay metadata que lo frene."""
    batch = recheck_batch([_recipient("wa_9")], {}, is_quiet_hours=_never_quiet)

    assert batch.recipients == [_recipient("wa_9")]
    assert batch.skipped == []
