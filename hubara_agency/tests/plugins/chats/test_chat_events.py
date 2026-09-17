"""Tests de `chat_events`: markers del historial → eventos estructurados.

El panel del chat pintaba cada envío no-textual como una línea de texto plano
("[el cliente tocó el botón: Ver catálogo]", "🔘 El bot envió botones: …").
El operador tenía que decodificar a mano qué fue un botón, qué escribió la
persona y qué describió la IA. Estos eventos le devuelven la forma real al
mensaje: los botones son botones, la foto es una foto, el caption es el caption.

Los markers los producen `sales/translate.py` (inbound) y
`sales/activities/flush_ui_intents.py::_build_history_event` (outbound). Este
módulo los LEE — por eso los tests de round-trip del final: si un productor
cambia su formato, acá truena, no en la UI.

Se parsea en vez de persistir campos nuevos al escribir porque así el historial
YA ESCRITO (todas las conversaciones del vault) también se ve con la forma
nueva, sin migración ni reprocesamiento.
"""
from __future__ import annotations

import asyncio

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _build_history_event,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import translate_to_effective_text
from src.plugins.chats.shared.chat_events import (
    annotate_touched_buttons,
    detect_chat_event,
)


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _bot_ui(component_kind: str, content: str) -> dict:
    return {
        "role": "assistant",
        "kind": "ui_component",
        "component_kind": component_kind,
        "content": content,
    }


# --- botones del bot -------------------------------------------------------


def test_bot_buttons_recovers_body_and_titles():
    event = detect_chat_event(
        _bot_ui(
            "quick_replies",
            "🔘 El bot envió botones: Ver catálogo · Asesoría · Envíos y pagos"
            " — con el mensaje: «Buenas tardes. Bienvenido a *Hubara*.»",
        )
    )
    assert event == {
        "kind": "bot_buttons",
        "body": "Buenas tardes. Bienvenido a *Hubara*.",
        "buttons": [
            {"title": "Ver catálogo"},
            {"title": "Asesoría"},
            {"title": "Envíos y pagos"},
        ],
    }


def test_bot_buttons_without_body():
    event = detect_chat_event(
        _bot_ui("quick_replies", "🔘 El bot envió botones: Sí · No")
    )
    assert event["body"] is None
    assert [b["title"] for b in event["buttons"]] == ["Sí", "No"]


# --- tap del cliente -------------------------------------------------------


def test_button_tap_recovers_the_title():
    assert detect_chat_event(_user("[el cliente tocó el botón: Ver catálogo]")) == {
        "kind": "button_tap",
        "title": "Ver catálogo",
    }


def test_button_tap_after_a_referral_banner():
    """El banner de CTWA se antepone al marker — el tap sigue siendo un tap."""
    event = detect_chat_event(
        _user(
            "[el cliente llega desde un anuncio de Meta]\n"
            "[el cliente tocó el botón: Ver catálogo]"
        )
    )
    assert event == {"kind": "button_tap", "title": "Ver catálogo"}


# --- foto del cliente ------------------------------------------------------


def test_customer_photo_splits_caption_from_vision():
    event = detect_chat_event(
        _user(
            '[el cliente envió una foto: Vela artesanal con figura de pareja'
            ' besándose.] con el texto: "Precio?"'
        )
    )
    assert event == {
        "kind": "customer_photo",
        "vision": "Vela artesanal con figura de pareja besándose.",
        "caption": "Precio?",
        "receipt": False,
    }


def test_customer_photo_without_caption():
    event = detect_chat_event(_user("[el cliente envió una foto: Una vela verde.]"))
    assert event["caption"] is None
    assert event["vision"] == "Una vela verde."


def test_payment_receipt_is_flagged():
    event = detect_chat_event(
        _user("[el cliente envió un comprobante de pago: Transferencia por $120.000.]")
    )
    assert event["kind"] == "customer_photo"
    assert event["receipt"] is True


def test_vision_failure_has_no_description():
    event = detect_chat_event(
        _user('[el cliente envió una imagen que no pude ver bien] con el texto: "Esta"')
    )
    assert event == {
        "kind": "customer_photo",
        "vision": None,
        "caption": "Esta",
        "receipt": False,
    }


# --- reacciones ------------------------------------------------------------


def test_customer_reaction_exposes_the_emoji():
    assert detect_chat_event(_user("[el cliente reaccionó con ❤️]")) == {
        "kind": "reaction",
        "emoji": "❤️",
        "author": "user",
    }


def test_legacy_reaction_without_emoji():
    """Historial viejo: el emoji se perdía al traducir. No se puede inventar."""
    assert detect_chat_event(_user("[el cliente envió un reaction]")) == {
        "kind": "reaction",
        "emoji": None,
        "author": "user",
    }


def test_bot_reaction_exposes_the_emoji():
    assert detect_chat_event(
        _bot_ui("reaction", "El bot reaccionó con 🤍 a un mensaje del cliente")
    ) == {"kind": "reaction", "emoji": "🤍", "author": "bot"}


# --- nada que proyectar ----------------------------------------------------


def test_plain_text_has_no_event():
    assert detect_chat_event(_user("Hola, cuánto vale la vela?")) is None


def test_other_ui_components_keep_their_note():
    """El catálogo y el resto siguen como nota de sistema — no los tocamos."""
    assert detect_chat_event(
        _bot_ui("products_list", "🛍️ El bot envió el catálogo con 24 productos")
    ) is None


# --- botón tocado ----------------------------------------------------------


def test_the_tapped_button_is_marked_inside_its_own_message():
    messages = [
        _bot_ui("quick_replies", "🔘 El bot envió botones: Ver catálogo · Asesoría"),
        _user("[el cliente tocó el botón: Ver catálogo]"),
    ]
    for m in messages:
        m["event"] = detect_chat_event(m)
    annotate_touched_buttons(messages)

    buttons = messages[0]["event"]["buttons"]
    assert buttons[0]["touched"] is True
    assert "touched" not in buttons[1]


def test_a_tap_does_not_reach_back_past_a_newer_button_message():
    """Dos tandas de botones con el mismo título: el tap marca la ÚLTIMA."""
    messages = [
        _bot_ui("quick_replies", "🔘 El bot envió botones: Confirmar"),
        _bot_ui("quick_replies", "🔘 El bot envió botones: Confirmar"),
        _user("[el cliente tocó el botón: Confirmar]"),
    ]
    for m in messages:
        m["event"] = detect_chat_event(m)
    annotate_touched_buttons(messages)

    assert "touched" not in messages[0]["event"]["buttons"][0]
    assert messages[1]["event"]["buttons"][0]["touched"] is True


def test_a_tap_without_a_matching_button_marks_nothing():
    messages = [
        _bot_ui("quick_replies", "🔘 El bot envió botones: Sí · No"),
        _user("[el cliente tocó el botón: Otra cosa]"),
    ]
    for m in messages:
        m["event"] = detect_chat_event(m)
    annotate_touched_buttons(messages)

    assert all("touched" not in b for b in messages[0]["event"]["buttons"])


# --- round-trip contra los productores reales ------------------------------
#
# Anti-drift: si `_build_history_event` o `translate_to_effective_text` cambian
# su formato, estos tests truenan acá (no en la UI, semanas después).


def test_round_trip_with_the_real_quick_replies_producer():
    produced = _build_history_event(
        "quick_replies",
        {
            "body": "¿Qué te gustaría saber?",
            "buttons": [{"title": "Ver catálogo"}, {"title": "Asesoría"}],
        },
    )
    event = detect_chat_event({**produced, "role": "assistant"})
    assert event["kind"] == "bot_buttons"
    assert [b["title"] for b in event["buttons"]] == ["Ver catálogo", "Asesoría"]
    assert event["body"] == "¿Qué te gustaría saber?"


def test_round_trip_with_the_real_button_reply_translation():
    msg = WhatsAppMessage(
        message_id="wamid.1",
        from_number="573001112233",
        phone_number_id="pnid",
        text=None,
        media=None,
        timestamp="2026-09-17T13:18:00Z",
        msg_type="interactive",
        interactive={"type": "button_reply", "id": "ver_catalogo", "title": "Ver catálogo"},
    )
    effective = asyncio.run(translate_to_effective_text(msg))
    assert detect_chat_event(_user(effective.text)) == {
        "kind": "button_tap",
        "title": "Ver catálogo",
    }


def test_round_trip_with_the_real_reaction_translation():
    msg = WhatsAppMessage(
        message_id="wamid.2",
        from_number="573001112233",
        phone_number_id="pnid",
        text=None,
        media={"type": "reaction", "message_id": "wamid.1", "emoji": "❤️"},
        timestamp="2026-09-17T13:19:00Z",
        msg_type="reaction",
    )
    effective = asyncio.run(translate_to_effective_text(msg))
    event = detect_chat_event(_user(effective.text))
    assert event == {"kind": "reaction", "emoji": "❤️", "author": "user"}
