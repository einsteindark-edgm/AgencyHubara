"""Tests del parser puro de WhatsApp Cloud API."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage, parse_whatsapp_inbound


def _envelope(value: dict) -> dict:
    return {"entry": [{"changes": [{"value": value}]}]}


def test_parse_text_message_returns_dataclass() -> None:
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.ABC",
                    "from": "5491111111111",
                    "timestamp": "1714312345",
                    "type": "text",
                    "text": {"body": "hola"},
                }
            ],
        }
    )

    parsed = parse_whatsapp_inbound(body)

    assert isinstance(parsed, WhatsAppMessage)
    assert parsed.message_id == "wamid.ABC"
    assert parsed.from_number == "5491111111111"
    assert parsed.phone_number_id == "PHONE_123"
    assert parsed.text == "hola"
    assert parsed.media is None
    assert parsed.timestamp == "1714312345"


def test_parse_media_message_populates_media_field() -> None:
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.IMG",
                    "from": "5491111111111",
                    "timestamp": "1714312400",
                    "type": "image",
                    "image": {"id": "media-id-xyz", "mime_type": "image/jpeg"},
                }
            ],
        }
    )

    parsed = parse_whatsapp_inbound(body)

    assert isinstance(parsed, WhatsAppMessage)
    assert parsed.text is None
    assert parsed.media is not None
    assert parsed.media["type"] == "image"
    assert parsed.media["id"] == "media-id-xyz"


@pytest.mark.parametrize(
    "evil_from",
    [
        "../../../../tmp/evil",
        "wa/../../etc",
        "573000000000/../..",
        "12345$(whoami)",
        "abc def",  # espacios
        "",  # vacío
    ],
)
def test_parse_rejects_non_phone_from_number(evil_from: str) -> None:
    """SEC-12: `from_number` se convierte en `session_id = wa_<from>` que llega al
    filesystem del vault. Un `from` que no sea un teléfono E.164 (dígitos) podría
    hacer path traversal fuera del vault → se rechaza en el parse."""
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.ABC",
                    "from": evil_from,
                    "timestamp": "1714312345",
                    "type": "text",
                    "text": {"body": "hola"},
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        parse_whatsapp_inbound(body)


def test_parse_status_update_returns_none() -> None:
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "statuses": [{"id": "wamid.OUT", "status": "delivered"}],
        }
    )

    assert parse_whatsapp_inbound(body) is None


def test_parse_malformed_body_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_whatsapp_inbound({"foo": "bar"})


def test_parse_text_message_without_body_raises_value_error() -> None:
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.ABC",
                    "from": "5491111111111",
                    "timestamp": "1714312345",
                    "type": "text",
                    "text": {},
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        parse_whatsapp_inbound(body)


# ── parse_whatsapp_inbound_all: el POST entero (Meta batchea) ────────────────

from src.plugins.chats.agent.sales.parsers import parse_whatsapp_inbound_all  # noqa: E402


def _text(wamid: str, from_number: str = "573001234567") -> dict:
    return {"id": wamid, "from": from_number, "timestamp": "1714312345", "type": "text", "text": {"body": "hola"}}


def test_parse_all_keeps_the_order_across_entries_changes_and_messages() -> None:
    value = lambda *msgs: {"value": {"metadata": {"phone_number_id": "PHONE_123"}, "messages": list(msgs)}}  # noqa: E731
    body = {"entry": [{"changes": [value(_text("wamid.1"), _text("wamid.2")), value(_text("wamid.3"))]}, {"changes": [value(_text("wamid.4"))]}]}

    batch = parse_whatsapp_inbound_all(body)

    assert [m.message_id for m in batch.messages] == ["wamid.1", "wamid.2", "wamid.3", "wamid.4"]
    assert batch.rejected == ()


def test_parse_all_reports_why_an_item_was_rejected_and_keeps_the_rest() -> None:
    body = {
        "entry": [
            {
                "changes": [
                    {"value": {"metadata": {"phone_number_id": "PHONE_123"}, "messages": [_text("wamid.OK"), {**_text("wamid.BAD"), "text": {}}]}},
                    {"value": {"messages": [_text("wamid.NOMETA")]}},
                    {"value": {"metadata": {"phone_number_id": "PHONE_123"}, "statuses": [{"id": "wamid.OUT"}]}},
                ]
            }
        ]
    }

    batch = parse_whatsapp_inbound_all(body)

    assert [m.message_id for m in batch.messages] == ["wamid.OK"]
    assert [r.reason for r in batch.rejected] == ["text message missing 'text.body'", "missing 'metadata.phone_number_id'"]
    assert [(r.wa_message_id, r.from_number) for r in batch.rejected] == [("wamid.BAD", "573001234567"), (None, None)]


@pytest.mark.parametrize("garbage", [None, [], "x", {}, {"entry": "x"}, {"entry": [None, {"changes": "x"}, {"changes": [None]}]}])
def test_parse_all_never_raises_on_garbage(garbage) -> None:
    batch = parse_whatsapp_inbound_all(garbage)

    assert batch.messages == ()


def test_parse_template_quick_reply_button_carries_title_and_payload() -> None:
    """Respuesta a un botón quick_reply de una PLANTILLA (carrusel de campaña):
    Meta manda `type: button` con `{payload, text}` (no `interactive`). El
    texto que ve el bot lleva el título Y el payload — así `ref: HUB-…` del
    botón "Me interesa" hidrata el producto como el botón del PDP."""
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.BTN",
                    "from": "5491111111111",
                    "timestamp": "1714312345",
                    "type": "button",
                    "button": {"payload": "ref: HUB-CUBOLOVE", "text": "Me interesa"},
                }
            ],
        }
    )
    parsed = parse_whatsapp_inbound(body)
    assert parsed is not None
    assert parsed.text == "Me interesa · ref: HUB-CUBOLOVE"
    assert parsed.media is None
    assert parsed.interactive == {
        "type": "template_button",
        "payload": "ref: HUB-CUBOLOVE",
        "title": "Me interesa",
    }


def test_parse_template_button_with_same_payload_and_text_is_not_duplicated() -> None:
    body = _envelope(
        {
            "metadata": {"phone_number_id": "PHONE_123"},
            "messages": [
                {
                    "id": "wamid.BTN2",
                    "from": "5491111111111",
                    "timestamp": "1714312345",
                    "type": "button",
                    "button": {"payload": "Sí, quiero", "text": "Sí, quiero"},
                }
            ],
        }
    )
    parsed = parse_whatsapp_inbound(body)
    assert parsed is not None
    assert parsed.text == "Sí, quiero"
