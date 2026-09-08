"""D1.4 — parser puro del webhook `standby` (MBA controla el hilo)."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.parsers import (
    StandbyEvent,
    WhatsAppMessage,
    echo_display_text,
    inbound_display_text,
    parse_whatsapp_inbound,
    parse_whatsapp_standby,
    parse_whatsapp_statuses,
    split_webhook_by_field,
    webhook_fields,
)
from tests.plugins.chats import standby_payloads as P


def test_standby_inbound_is_parsed_like_a_regular_inbound() -> None:
    event = parse_whatsapp_standby(P.inbound("hola"))
    assert isinstance(event, StandbyEvent) and event.phone_number_id == P.PHONE_NUMBER_ID
    assert len(event.messages) == 1 and not event.echoes and not event.statuses
    msg = event.messages[0]
    assert isinstance(msg, WhatsAppMessage)
    assert (msg.message_id, msg.from_number, msg.text, msg.msg_type) == ("wamid.STANDBY.IN.1", P.CUSTOMER, "hola", "text")
    assert inbound_display_text(msg) == "hola"
    img = parse_whatsapp_standby(P.inbound_image()).messages[0]
    assert img.media and img.media["type"] == "image" and img.text is None
    assert inbound_display_text(img) == "[imagen] mi comprobante"
    ad = parse_whatsapp_standby(P.inbound_with_referral()).messages[0]
    assert ad.referral == {"source_url": "https://fb.me/ad1", "source_id": "AD_1", "source_type": "ad",
                           "headline": "Velas", "ctwa_clid": "CLID_1"}


def test_standby_echo_keeps_the_exact_body_mba_sent() -> None:
    event = parse_whatsapp_standby(P.echo_text("Listo, tu pedido quedó registrado 🤍"))
    assert not event.messages and not event.statuses and len(event.echoes) == 1
    echo = event.echoes[0]
    assert (echo.wamid, echo.to, echo.timestamp, echo.msg_type) == ("wamid.STANDBY.ECHO.1", P.CUSTOMER, "1757300020", "text")
    assert echo.text == "Listo, tu pedido quedó registrado 🤍" and echo.template_name is None
    assert echo.message["text"]["body"] == "Listo, tu pedido quedó registrado 🤍"
    assert echo_display_text(echo) == "Listo, tu pedido quedó registrado 🤍"

    tpl = parse_whatsapp_standby(P.echo_template()).echoes[0]
    assert tpl.msg_type == "template" and tpl.template_name == "summer_sale_2026" and tpl.text is None
    assert echo_display_text(tpl) == "[plantilla summer_sale_2026]"

    flow = parse_whatsapp_standby(P.echo_flow()).echoes[0]
    assert flow.msg_type == "interactive" and flow.text == "Schedule your visit with us."
    assert echo_display_text(flow) == "[flow] Schedule your visit with us."


def test_standby_statuses_carry_pricing_with_metas_type_key() -> None:
    event = parse_whatsapp_standby(P.status())
    assert len(event.statuses) == 1
    st = event.statuses[0]
    assert st.wa_message_id == "wamid.STANDBY.ECHO.1" and st.status == "delivered"
    assert st.pricing == {"billable": True, "pricing_model": "PMP", "category": "utility", "type": "regular"}


def test_standby_never_enters_the_legacy_inbound_or_status_path() -> None:
    """Todo va bajo `value.standby`: el parser legacy no ve messages ni statuses
    (así un standby NUNCA arranca el workflow Sales por la puerta vieja)."""
    for body in (P.inbound(), P.echo_text(), P.status()):
        assert parse_whatsapp_inbound(body) is None
        assert parse_whatsapp_statuses(body) == []
    assert webhook_fields(P.inbound()) == {"standby"}
    assert webhook_fields(P.handover()) == {"messaging_handovers"}
    assert webhook_fields({"entry": [{"changes": [{"value": {}}]}]}) == set()


def test_a_body_without_standby_changes_yields_none_and_grouped_changes_are_all_read() -> None:
    assert parse_whatsapp_standby({"entry": [{"changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": "x"}}}]}]}) is None
    assert parse_whatsapp_standby({"object": "whatsapp_business_account", "entry": []}) is None
    assert parse_whatsapp_standby("nope") is None
    event = parse_whatsapp_standby(P.merged(P.inbound(), P.echo_text(), P.status()))
    assert (len(event.messages), len(event.echoes), len(event.statuses)) == (1, 1, 1)


@pytest.mark.parametrize("bad_to", ["../../etc", "wa_1", "+573001234567", ""])
def test_standby_echo_with_an_unsafe_recipient_is_dropped(bad_to: str) -> None:
    body = P.echo_text()
    body["entry"][0]["changes"][0]["value"]["standby"]["message_echoes"][0]["message"]["to"] = bad_to
    assert parse_whatsapp_standby(body).echoes == ()


def test_standby_inbound_with_an_unsafe_sender_is_dropped_not_raised() -> None:
    body = P.inbound()
    body["entry"][0]["changes"][0]["value"]["standby"]["messages"][0]["from"] = "../x"
    assert parse_whatsapp_standby(body).messages == ()


def test_standby_ids_of_arbitrary_length_are_dropped() -> None:
    """El wamid entra al vault (dedupe): un router público no acepta ids sin tope."""
    long_id = "wamid." + "x" * 200
    body = P.inbound(wamid=long_id)
    assert parse_whatsapp_standby(body).messages == ()
    body = P.echo_text(wamid=long_id)
    assert parse_whatsapp_standby(body).echoes == ()


def test_split_webhook_by_field_gives_each_handler_only_its_changes() -> None:
    legacy_change = {"value": {"metadata": {"phone_number_id": "x"}, "messages": []}}  # sin `field` (simulado)
    body = P.merged(P.inbound(), P.echo_text(), P.handover())
    body["entry"][0]["changes"].append(legacy_change)
    parts = split_webhook_by_field(body)
    assert set(parts) == {"standby", "messaging_handovers", "messages"}
    assert [c["field"] for c in parts["standby"]["entry"][0]["changes"]] == ["standby", "standby"]
    assert parts["messages"]["entry"][0]["changes"] == [legacy_change]
    assert parts["messages"]["object"] == "whatsapp_business_account"
    assert split_webhook_by_field({"entry": []}) == {}
    assert split_webhook_by_field("nope") == {}
