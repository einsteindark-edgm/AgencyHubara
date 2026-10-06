"""Las tres reglas de lectura del ingest, partidas en LEER y ESCRIBIR (motor
de decisiones F2, enchufe 1).

Hoy cada regla lee el texto del cliente y escribe el metadata en un solo
paso. Para que el motor pueda poner a Jev (o la regla) en la LECTURA sin
tocar la escritura, cada una se parte en dos funciones puras; la de siempre
queda como la composición de las dos y hace exactamente lo mismo:

* compra: `classify_inbound_purchase_signal` + `apply_inbound_purchase_signal`
* retoma: `parse_reengagement_deferral` + `is_courtesy_text` + `apply_reengagement_deferral`
* baja:   `has_recent_marketing_context` + `is_opt_out_text`
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from src.plugins.chats.shared.purchase_signals import (
    apply_inbound_purchase_signal,
    classify_inbound_purchase_signal,
    register_inbound_purchase_signals,
)
from src.sdk.messagingkit import (
    apply_reengagement_deferral,
    detect_marketing_opt_out,
    has_recent_marketing_context,
    is_courtesy_text,
    is_opt_out_text,
    parse_reengagement_deferral,
    register_reengagement_deferral,
)

NOW = 1_790_000_000_000
TZ = ZoneInfo("America/Bogota")


def _draft_md() -> dict:
    return {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None, "order_draft": {"slots": {"producto": "Cubo Love"}}}]}


@pytest.mark.parametrize(
    ("text", "interactive", "order", "expected"),
    [
        ("sí, dale", None, None, ("affirmation", "text")),
        ("Voy apenas en camino a casa", None, None, ("deferral", "text")),
        ("¿cuánto vale?", None, None, (None, "text")),
        (None, {"type": "button_reply", "id": "order.confirm", "title": "Confirmar"}, None, ("affirmation", "button")),
        (None, None, {"product_items": [{"product_retailer_id": "x"}]}, ("affirmation", "cart")),
    ],
)
def test_classify_reads_the_purchase_signal_without_writing(text, interactive, order, expected) -> None:
    assert classify_inbound_purchase_signal(text, interactive=interactive, order=order) == expected


@pytest.mark.parametrize("text", ["sí, dale", "Voy apenas en camino a casa", "¿cuánto vale?", "Ok, lavanda"])
def test_register_is_classify_then_apply(text: str) -> None:
    by_register, by_parts = _draft_md(), _draft_md()

    kind = register_inbound_purchase_signals(by_register, text, now_ms=NOW, message_id="w1")
    signal, source = classify_inbound_purchase_signal(text)
    applied = apply_inbound_purchase_signal(by_parts, signal, source, now_ms=NOW, message_id="w1", text=text)

    assert kind == applied and by_register == by_parts


@pytest.mark.parametrize(
    "text",
    ["les escribo la otra semana", "gracias", "¿y el envío?", "mañana te confirmo", "Te confirmo, sí la quiero"],
)
def test_the_deferral_register_is_parse_courtesy_then_apply(text: str) -> None:
    base = {"reengagement_deferral": {"at_ms": NOW - 1000, "until_ms": NOW + 86_400_000, "kind": "open", "text": "luego"}}
    by_register, by_parts = dict(base), dict(base)

    register_reengagement_deferral(by_register, text, now_ms=NOW, tz=TZ)
    apply_reengagement_deferral(
        by_parts, text, parse_reengagement_deferral(text, NOW, TZ), courtesy=is_courtesy_text(text), now_ms=NOW, tz=TZ
    )

    assert by_register == by_parts


def test_the_opt_out_needs_a_recent_campaign_and_the_phrase() -> None:
    recent = {"campaign_touches": [{"campaign_id": "c1", "sent_at_ms": NOW - 3_600_000}]}

    assert is_opt_out_text("no más") and not is_opt_out_text("¿tienen velas rojas?")
    for metadata in ({}, recent):
        for text in ("no más", "¿tienen velas rojas?"):
            expected = has_recent_marketing_context(metadata, NOW) and is_opt_out_text(text)
            assert detect_marketing_opt_out(text, metadata, NOW) == expected
