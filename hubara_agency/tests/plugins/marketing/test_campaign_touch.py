"""El touch de campaña en el metadata del contacto — idempotente por
(campaña, contacto).

El envío por lotes reintenta (activity, lote hijo, worker que se cae entre
el write y el ack): un touch repetido duplicaba la atribución en Ads y el
contexto que ve el bot. Un contacto recibe UNA vez cada campaña real; las
pruebas del operador son otra cosa (no compiten con el touch real).
"""
from src.plugins.marketing.domain.campaigns import append_campaign_touch


def _touch(campaign_id: str = "mkt-1", **over) -> dict:
    return {"campaign_id": campaign_id, "campaign_name": "Promo", "sent_at_ms": 1_000, **over}


def test_stamping_the_same_campaign_twice_leaves_a_single_touch() -> None:
    metadata: dict = {}

    append_campaign_touch(metadata, _touch(wa_message_id="wamid-1"))
    append_campaign_touch(metadata, _touch(sent_at_ms=2_000, wa_message_id="wamid-1"))

    assert metadata["campaign_touches"] == [_touch(wa_message_id="wamid-1")]


def test_an_operator_test_send_does_not_swallow_the_real_send() -> None:
    """El operador se prueba la campaña en su número y después la campaña
    real le llega igual: los dos touches quedan (la atribución ignora el de
    prueba; el bot ve el real)."""
    metadata: dict = {}

    append_campaign_touch(metadata, _touch(test=True))
    append_campaign_touch(metadata, _touch(sent_at_ms=2_000))

    assert metadata["campaign_touches"] == [_touch(test=True), _touch(sent_at_ms=2_000)]


def test_other_campaigns_still_add_their_own_touch() -> None:
    metadata: dict = {}

    append_campaign_touch(metadata, _touch("mkt-1"))
    append_campaign_touch(metadata, _touch("mkt-2"))

    assert [t["campaign_id"] for t in metadata["campaign_touches"]] == ["mkt-1", "mkt-2"]
