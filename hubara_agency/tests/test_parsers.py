"""Tests del parser puro de WhatsApp Cloud API."""
from __future__ import annotations

import pytest

from src.sales_whatsapp.parsers import WhatsAppMessage, parse_whatsapp_inbound


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
