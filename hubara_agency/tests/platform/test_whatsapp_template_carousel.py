"""Plantillas MARKETING con CARRUSEL de productos (Meta: media card carousel).

Una campaña puede llevar 2..10 tarjetas, cada una con la foto del producto
(media_id subido a Meta), un cuerpo corto (nombre · precio) y un botón
quick_reply cuyo payload devuelve `ref: HUB-<sku>` — el ingest de chats ya
hidrata el producto desde ese ref. Meta fija la cantidad de tarjetas AL CREAR
la plantilla, así que hay una plantilla por cantidad (`..._v1_{n}`).

Contrato:
  * El catálogo declara `carousel_cards: n` (y un rango expande n entradas).
  * El builder emite `body` + `carousel.cards[]` (header IMAGE por id, body,
    botón quick_reply con payload) y se niega si la cantidad no matchea o si
    se pasan tarjetas a una plantilla sin carrusel.
  * `send_template_to_session` pasa las tarjetas al cliente, las incluye en
    el fingerprint de idempotencia y deja los productos en el historial.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.platform.whatsapp.activities import send_template_to_session
from src.platform.whatsapp.dtos import CarouselCard, OutboundResult
from src.platform.whatsapp.outbound import build_template_message
from src.platform.whatsapp.templates.registry import (
    _build_registry_from_dict,
    carousel_template_name,
    load_template_registry_from_yaml,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFINITIONS = _REPO_ROOT / "infra" / "whatsapp-provisioning" / "definitions" / "templates.json"


def _entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "name": "carousel_v1_{n}",
        "category": "marketing",
        "language": "es_CO",
        "waba_template_name": "carousel_{n}_v1",
        "semantics": "...",
        "triggers_when_window_expiring": False,
        "body": "¡{{1}}! {{2}}",
        "carousel_cards_range": [2, 3],
        "variables": [
            {"name": "greeting", "type": "string", "max_length": 80},
            {"name": "message", "type": "string", "max_length": 640},
        ],
    }
    entry.update(overrides)
    return entry


def _cards(n: int) -> list[CarouselCard]:
    return [
        CarouselCard(
            header_media_id=f"MEDIA{i}",
            body_text=f"Producto {i} · $45.000",
            quick_reply_payload=f"ref: HUB-P{i}",
        )
        for i in range(n)
    ]


# ---------- catálogo ----------


def test_catalog_range_expands_one_spec_per_card_count():
    registry = _build_registry_from_dict({"templates": [_entry()]})
    assert set(registry) == {"carousel_v1_2", "carousel_v1_3"}
    assert registry["carousel_v1_2"].carousel_cards == 2
    assert registry["carousel_v1_3"].carousel_cards == 3
    assert registry["carousel_v1_3"].waba_template_name == "carousel_3_v1"
    assert [v.name for v in registry["carousel_v1_3"].variables] == [
        "greeting",
        "message",
    ]


def test_catalog_fixed_carousel_count_and_default_zero():
    entry = _entry(name="c5", waba_template_name="c5", carousel_cards=5)
    entry.pop("carousel_cards_range")
    registry = _build_registry_from_dict({"templates": [entry]})
    assert registry["c5"].carousel_cards == 5
    plain = _build_registry_from_dict({"templates": [{**entry, "carousel_cards": 0}]})
    assert plain["c5"].carousel_cards == 0


def test_catalog_rejects_carousel_outside_meta_limits():
    for bad in (1, 11):
        entry = _entry(name="c", waba_template_name="c", carousel_cards=bad)
        entry.pop("carousel_cards_range")
        with pytest.raises(ValueError, match="carousel_cards"):
            _build_registry_from_dict({"templates": [entry]})


def test_real_catalog_has_campaign_carousel_for_2_to_10_cards():
    registry = load_template_registry_from_yaml()
    for n in range(2, 11):
        name = carousel_template_name(n)
        assert name in registry, name
        spec = registry[name]
        assert spec.category == "marketing"
        assert spec.triggers_when_window_expiring is False
        assert spec.carousel_cards == n
        # Mismas 3 variables que la plantilla de campaña sin carrusel.
        assert [v.name for v in spec.variables] == [
            "greeting",
            "campaign_message",
            "campaign_offer",
        ]
        assert spec.waba_template_name == f"campaign_carousel_{n}_marketing_v1"


def test_provisioning_defines_every_carousel_template_with_matching_cards():
    definitions = {d["name"]: d for d in json.loads(_DEFINITIONS.read_text(encoding="utf-8"))}
    registry = load_template_registry_from_yaml()
    for n in range(2, 11):
        spec = registry[carousel_template_name(n)]
        definition = definitions[spec.waba_template_name]
        assert definition["category"] == "MARKETING"
        carousel = definition["carousel"]
        assert carousel["cards"] == n
        assert carousel["header"]["format"] == "IMAGE"
        assert (_DEFINITIONS.parent / carousel["header"]["example_file"]).is_file()
        # Una variable en el cuerpo de la tarjeta (nombre · precio) + quick reply.
        assert carousel["card_body"].count("{{") == 1
        assert carousel["buttons"][0]["type"] == "QUICK_REPLY"
        assert definition["body"].count("{{") == len(spec.variables)
        assert "no recibir más" in definition["body"]


# ---------- builder ----------


def _spec(n: int = 2):
    return _build_registry_from_dict({"templates": [_entry()]})[f"carousel_v1_{n}"]


def test_builder_emits_body_then_carousel_cards():
    payload = build_template_message(
        "573001234567",
        _spec(2),
        {"greeting": "Hola", "message": "Mirá esto"},
        carousel_cards=_cards(2),
    )
    components = payload["template"]["components"]
    assert components[0] == {
        "type": "body",
        "parameters": [
            {"type": "text", "text": "Hola"},
            {"type": "text", "text": "Mirá esto"},
        ],
    }
    assert components[1]["type"] == "carousel"
    cards = components[1]["cards"]
    assert [c["card_index"] for c in cards] == [0, 1]
    assert cards[1]["components"] == [
        {"type": "header", "parameters": [{"type": "image", "image": {"id": "MEDIA1"}}]},
        {"type": "body", "parameters": [{"type": "text", "text": "Producto 1 · $45.000"}]},
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "0",
            "parameters": [{"type": "payload", "payload": "ref: HUB-P1"}],
        },
    ]


def test_builder_refuses_wrong_card_count():
    with pytest.raises(ValueError, match="tarjetas"):
        build_template_message(
            "573001234567",
            _spec(3),
            {"greeting": "Hola", "message": "x"},
            carousel_cards=_cards(2),
        )


def test_builder_refuses_missing_cards_on_carousel_template():
    with pytest.raises(ValueError, match="tarjetas"):
        build_template_message("573001234567", _spec(2), {"greeting": "Hola", "message": "x"})


def test_builder_refuses_cards_on_plain_template():
    registry = load_template_registry_from_yaml()
    with pytest.raises(ValueError, match="carrusel"):
        build_template_message(
            "573001234567",
            registry["human_followup_utility_v1"],
            {"followup_message": "tu vela"},
            carousel_cards=_cards(2),
        )


# ---------- envío ----------


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")
    return tmp_path


def _history(vault: Path, session_id: str) -> list[dict]:
    path = vault / session_id / "sessions" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_send_passes_cards_and_keeps_products_in_history(isolated_vault, monkeypatch):
    """Número SIN sesión previa (audiencia importada): el envío igual sale con
    el número del negocio de env y deja historial + metadata."""
    mock_send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.C", ok=True))
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )
    cards = _cards(2)
    variables = {
        "greeting": "Hola",
        "campaign_message": "Mirá lo nuevo.",
        "campaign_offer": "Escríbeme aquí y te cuento más.",
    }

    result = await send_template_to_session(
        "wa_573001234567",
        carousel_template_name(2),
        variables,
        carousel_cards=cards,
    )

    assert result.ok
    assert mock_send.call_args.args[0] == "PHONE_ENV"
    assert mock_send.call_args.kwargs["carousel_cards"] == cards
    [event] = _history(isolated_vault, "wa_573001234567")
    assert "Producto 0 · $45.000" in event["content"]
    assert "Producto 1 · $45.000" in event["content"]
    assert event["carousel"] == [
        {"body_text": "Producto 0 · $45.000", "quick_reply_payload": "ref: HUB-P0"},
        {"body_text": "Producto 1 · $45.000", "quick_reply_payload": "ref: HUB-P1"},
    ]
    assert (isolated_vault / "wa_573001234567" / "metadata.json").is_file()


@pytest.mark.asyncio
async def test_same_text_with_different_cards_is_not_deduped(isolated_vault, monkeypatch):
    mock_send = AsyncMock(
        side_effect=[
            OutboundResult(wa_message_id="wamid.1", ok=True),
            OutboundResult(wa_message_id="wamid.2", ok=True),
        ]
    )
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )
    variables = {
        "greeting": "Hola",
        "campaign_message": "Mirá lo nuevo.",
        "campaign_offer": "Escríbeme aquí y te cuento más.",
    }
    other = [
        CarouselCard(header_media_id="X", body_text="Otro", quick_reply_payload="ref: HUB-O"),
        CarouselCard(header_media_id="Y", body_text="Otro 2", quick_reply_payload="ref: HUB-O2"),
    ]
    for cards in (_cards(2), other):
        await send_template_to_session(
            "wa_573001234567", carousel_template_name(2), variables, carousel_cards=cards
        )
    assert mock_send.await_count == 2
