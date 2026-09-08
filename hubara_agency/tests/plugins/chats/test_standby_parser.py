"""D1.4 — parser puro del webhook `standby` (MBA controla el hilo)."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.parsers import (
    HandoversEvent,
    StandbyEvent,
    WhatsAppMessage,
    echo_display_text,
    inbound_display_text,
    parse_messaging_handovers,
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


# ── D1.5: `messaging_handovers` → quién controla el hilo ─────────────────────


def test_control_taken_is_parsed_with_both_app_ids_and_the_customer() -> None:
    ev = parse_messaging_handovers(P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300060", metadata="tomado al enviar"))
    assert isinstance(ev, HandoversEvent) and ev.phone_number_id == P.PHONE_NUMBER_ID
    assert len(ev.handovers) == 1 and ev.unparsed == 0
    h = ev.handovers[0]
    assert (h.customer, h.kind, h.new_owner_app_id, h.previous_owner_app_id, h.timestamp_ms, h.metadata) == (
        P.CUSTOMER, "control_taken", P.OUR_APP_ID, P.MBA_APP_ID, 1757300060000, "tomado al enviar"
    )


def test_messenger_style_pass_thread_control_with_int_ids_and_ms_timestamp_is_accepted() -> None:
    body = P.handover_messenger_style(P.MBA_APP_ID, P.OUR_APP_ID)
    item = body["entry"][0]["changes"][0]["value"]["messaging_handovers"][0]
    item["pass_thread_control"]["new_owner_app_id"] = 123456789  # Messenger lo documenta como int en take_thread_control
    h = parse_messaging_handovers(body).handovers[0]
    assert h.kind == "pass_thread_control" and h.new_owner_app_id == "123456789"
    assert h.previous_owner_app_id == P.OUR_APP_ID and h.metadata == "release"
    assert h.timestamp_ms == 1757300060000 and h.customer == P.CUSTOMER


@pytest.mark.parametrize("bad_customer", ["../x", "", "+573001234567", "wa_573001234567"])
def test_a_handover_with_an_unsafe_customer_is_counted_as_unparsed_not_raised(bad_customer: str) -> None:
    ev = parse_messaging_handovers(P.handover(customer=bad_customer))
    assert ev.handovers == () and ev.unparsed == 1


def test_a_handover_without_a_control_block_or_a_sender_is_unparsed() -> None:
    body = P.handover()
    item = body["entry"][0]["changes"][0]["value"]["messaging_handovers"][0]
    del item["sender"]
    assert parse_messaging_handovers(body).unparsed == 1
    body = P.handover()
    item = body["entry"][0]["changes"][0]["value"]["messaging_handovers"][0]
    del item["control_taken"]
    ev = parse_messaging_handovers(body)
    assert ev.handovers == () and ev.unparsed == 1


def test_a_null_new_owner_is_parsed_as_none_not_dropped() -> None:
    h = parse_messaging_handovers(P.handover(new_owner=None, previous_owner=P.OUR_APP_ID)).handovers[0]
    assert h.new_owner_app_id is None and h.previous_owner_app_id == P.OUR_APP_ID


def test_bodies_without_handovers_yield_none_and_the_other_parsers_ignore_handovers() -> None:
    assert parse_messaging_handovers(P.inbound()) is None
    assert parse_messaging_handovers("nope") is None
    assert parse_messaging_handovers({"entry": []}) is None
    assert parse_whatsapp_standby(P.handover()) is None
    assert parse_whatsapp_inbound(P.handover()) is None
    assert parse_whatsapp_statuses(P.handover()) == []
    ev = parse_messaging_handovers(P.merged(P.handover(), P.handover_messenger_style()))
    assert [h.kind for h in ev.handovers] == ["control_taken", "pass_thread_control"]
