"""Plantillas con CARRUSEL (media cards) en el CLI de provisioning.

Meta fija la cantidad de tarjetas al crear la plantilla: la definición dice
`carousel.cards = n` y el CLI emite BODY + CAROUSEL con n tarjetas idénticas
(header IMAGE con la foto de ejemplo subida, body con su ejemplo y el botón
quick_reply). Solo stdlib + pytest; la subida se reemplaza por un grabador."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "whatsapp_provision.py"
_spec = importlib.util.spec_from_file_location("whatsapp_provision", _SCRIPT)
wp = importlib.util.module_from_spec(_spec)
sys.modules["whatsapp_provision"] = wp
_spec.loader.exec_module(wp)

CFG = {"APP_ID": "3662", "WABA_ID": "1763", "SYSTEM_USER_TOKEN": "EAA-token"}

CAROUSEL_DEF = {
    "name": "campaign_carousel_3_marketing_v1",
    "category": "MARKETING",
    "language": "es_CO",
    "body": "¡{{1}}! {{2}} {{3}} Si prefieres no recibir más promociones, respóndeme \"NO MÁS\".",
    "example": ["Hola Camila", "Mirá lo nuevo.", "Escríbeme aquí."],
    "carousel": {
        "cards": 3,
        "header": {"format": "IMAGE", "example_file": "assets/order_ready_example.jpg"},
        "card_body": "{{1}}",
        "card_example": ["Vela Buda Zen · $45.000"],
        "buttons": [{"type": "QUICK_REPLY", "text": "Me interesa"}],
    },
}


def test_carousel_definition_emits_body_and_n_identical_cards(monkeypatch) -> None:
    uploads = []

    def fake_upload(cfg, path):
        uploads.append(path)
        return "4::HANDLE"

    monkeypatch.setattr(wp, "_resumable_upload", fake_upload)

    components = wp._template_components(CFG, CAROUSEL_DEF)

    assert [c["type"] for c in components] == ["BODY", "CAROUSEL"]
    assert components[0]["example"] == {
        "body_text": [["Hola Camila", "Mirá lo nuevo.", "Escríbeme aquí."]]
    }
    cards = components[1]["cards"]
    assert len(cards) == 3
    # La foto de ejemplo se sube UNA vez y se reutiliza en todas las tarjetas.
    assert len(uploads) == 1 and uploads[0].endswith("assets/order_ready_example.jpg")
    for card in cards:
        assert card["components"] == [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["4::HANDLE"]}},
            {"type": "BODY", "text": "{{1}}", "example": {"body_text": [["Vela Buda Zen · $45.000"]]}},
            {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Me interesa"}]},
        ]


def test_carousel_definition_without_example_photo_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(wp, "_resumable_upload", lambda cfg, path: None)
    assert wp._template_components(CFG, CAROUSEL_DEF) is None
