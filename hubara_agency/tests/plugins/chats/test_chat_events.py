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

La excepción es la tarjeta (`card`) que el flush guarda con cada envío: el
mensaje entero que leyó el cliente, que la línea recorta (tests de tarjetas).
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
    """Historial viejo: el catálogo sin tarjeta (`card`) sigue como nota."""
    assert detect_chat_event(
        _bot_ui("products_list", "🛍️ El bot envió el catálogo con 24 productos")
    ) is None


# --- tarjetas: el mensaje entero que leyó el cliente -----------------------
#
# Caso 2026-10-13 (run 2395ffd6): el bot saludó dentro del texto del catálogo y
# el panel solo decía «El bot envió el catálogo con 2 productos».


def test_round_trip_a_catalog_is_a_card_with_its_message_and_products():
    greeting = "Buenas tardes, bienvenido a *Hubara*.\n\nPara la Noche de Velitas tenemos:"
    produced = _build_history_event("products_list", {
        "intro_text": greeting,
        "sections": [{"title": "Religiosas", "rows": [
            {"id": "a", "title": "Velas de deseos onduladas", "description": "$22.500 COP"},
            {"id": "b", "title": "Velas de deseos cuadradas"},
        ]}],
    })
    assert detect_chat_event(produced) == {
        "kind": "bot_card",
        "component": "products_list",
        # Qué fue y su ícono los decide el servidor: la app y el panel los
        # pintan sin conocer el componente (uno nuevo no pide otra app).
        "label": "Catálogo",
        "icon": "catalog",
        "title": None,
        "text": greeting,
        "items": [
            {"title": "Velas de deseos onduladas", "detail": "$22.500 COP"},
            {"title": "Velas de deseos cuadradas", "detail": None},
        ],
        "buttons": [],
        "action": None,
        "link": None,
        "images": [],
    }


def test_round_trip_a_link_button_and_a_photo_are_cards():
    link = detect_chat_event(_build_history_event("cta_url", {
        "url": "https://pago.hubara.co/x", "button_text": "Pagar", "body_text": "Paga aquí",
    }))
    assert link["kind"] == "bot_card" and link["text"] == "Paga aquí"
    assert link["link"] == {"label": "Pagar", "url": "https://pago.hubara.co/x"}

    photo = detect_chat_event(_build_history_event(
        "product_detail", {"image_url": "https://cdn/x.jpg", "caption": "Vela Cruz"}
    ))
    assert photo["kind"] == "bot_card" and photo["component"] == "product_detail"
    assert photo["images"] == [{"url": "https://cdn/x.jpg", "caption": "Vela Cruz"}]


def test_round_trip_long_buttons_message_is_whole():
    """La línea del historial recorta el cuerpo; los botones muestran el de
    la tarjeta, entero."""
    body = "Buenas tardes, bienvenido a Hubara. " + "Te cuento de nuestras velas. " * 12
    produced = _build_history_event(
        "quick_replies", {"body": body, "buttons": [{"title": "Ver catálogo"}]}
    )
    event = detect_chat_event(produced)
    assert event["kind"] == "bot_buttons"
    assert event["body"] == body.strip()
    assert [b["title"] for b in event["buttons"]] == ["Ver catálogo"]


def test_round_trip_form_order_summary_and_contact_say_what_they_are():
    form = detect_chat_event(_build_history_event(
        "shipping_flow", {"body": "Necesito unos datos", "flow_cta": "Completar datos"}
    ))
    assert (form["label"], form["icon"], form["action"]) == ("Formulario de envío", "form", "Completar datos")

    summary = detect_chat_event(_build_history_event("order_confirmation", {
        "items": [{"title": "Velón Koala", "quantity": 1, "unit_price_cop": 35000}],
        "subtotal_cop": 35000, "total_cop": 35000, "payment_method": "transfer",
    }))
    assert (summary["label"], summary["icon"]) == ("Resumen para confirmar", "order")
    assert [b["title"] for b in summary["buttons"]] == ["✅ Confirmar", "✏️ Modificar", "❌ Cancelar"]


def test_a_component_the_server_does_not_name_still_shows_as_a_message():
    event = detect_chat_event({**_bot_ui("holograma", "📤 …"), "card": {"text": "Hola"}})
    assert (event["label"], event["icon"]) == ("Mensaje interactivo", "message")


def test_the_tapped_order_button_is_marked_inside_the_summary():
    """El cliente tocó «✅ Confirmar» del resumen: queda marcado ahí, como en
    los botones de respuesta."""
    messages = [
        detect_chat_event(_build_history_event("order_confirmation", {"items": [], "total_cop": 0})),
        detect_chat_event(_user("[el cliente tocó el botón: ✅ Confirmar]")),
    ]
    wrapped = [{"event": e} for e in messages]
    annotate_touched_buttons(wrapped)
    assert wrapped[0]["event"]["buttons"][0] == {"title": "✅ Confirmar", "touched": True}
    assert "touched" not in wrapped[0]["event"]["buttons"][1]


def test_a_broken_card_falls_back_to_the_note():
    """Un `card` mal formado (escrito a mano, versión rara) no tumba el chat:
    lo que no sirve se descarta, y sin nada que mostrar queda la nota."""
    broken = {**_bot_ui("products_list", "🛍️ El bot envió el catálogo con 2 productos"),
              "card": {"text": 5, "items": "x", "images": [{"caption": "sin url"}]}}
    assert detect_chat_event(broken) is None

    partial = {**_bot_ui("products_list", "🛍️ …"),
               "card": {"items": [{"title": "Vela"}, {"detail": "sin título"}, "x"]}}
    assert detect_chat_event(partial)["items"] == [{"title": "Vela", "detail": None}]


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


def test_round_trip_buttons_sent_by_the_operator_from_the_app_are_still_buttons():
    """La app móvil corre `send_quick_replies` como el humano: la nota dice
    "El operador envió botones" y el panel los sigue pintando como botones."""
    produced = _build_history_event(
        "quick_replies",
        {"body": "¿Te lo envío hoy?", "buttons": [{"title": "Sí, hoy"}, {"title": "Más tarde"}]},
        actor="El operador",
    )
    event = detect_chat_event({**produced, "role": "assistant", "sender": "human"})
    assert event is not None and event["kind"] == "bot_buttons"
    assert [b["title"] for b in event["buttons"]] == ["Sí, hoy", "Más tarde"]
    assert event["body"] == "¿Te lo envío hoy?"


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


# --- formulario de envío (Flow nfm_reply) ------------------------------------


def test_shipping_form_becomes_a_structured_card():
    event = detect_chat_event(
        _user(
            "[datos de envío recibidos] city=Bogotá ; neighborhood=Las Nieves; "
            "address=Calle 1 #2-3 apto 4, edificio Prueba; phone=3001112233; "
            "receiver_name=Ana Prueba; payment_method=transfer; "
            "order_total_cop=45000; items_summary=1× Trilogía del Terror; "
            "flow_token=shipping_wa_test_1"
        )
    )
    assert event == {
        "kind": "shipping_form",
        "receiver_name": "Ana Prueba",
        "phone": "3001112233",
        "city": "Bogotá",
        "neighborhood": "Las Nieves",
        "address": "Calle 1 #2-3 apto 4, edificio Prueba",
        "payment_method": "transfer",
        "order_total_cop": 45000,
        "items_summary": "1× Trilogía del Terror",
        # flow_token es plomería interna: no se muestra al operador.
        "extra": [],
    }


def test_shipping_form_keeps_unknown_fields_and_tolerates_gaps():
    event = detect_chat_event(
        _user("[datos de envío recibidos] city=Cali; notes=Portería 24h; order_total_cop=abc")
    )
    assert event["city"] == "Cali"
    assert event["address"] is None
    assert event["order_total_cop"] is None
    assert event["extra"] == [{"key": "notes", "value": "Portería 24h"}]


def test_empty_shipping_form_marker_still_is_a_card():
    event = detect_chat_event(_user("[datos de envío recibidos]"))
    assert event["kind"] == "shipping_form"
    assert event["city"] is None and event["extra"] == []


def test_round_trip_with_the_real_flow_translation():
    msg = WhatsAppMessage(
        message_id="wamid.3",
        from_number="573001112233",
        phone_number_id="pnid",
        text=None,
        media=None,
        timestamp="2026-09-17T13:20:00Z",
        msg_type="interactive",
        interactive={
            "type": "nfm_reply",
            "name": "flow",
            "response_json": {
                "city": "Medellín",
                "address": "Cra 1 # 2-3",
                "order_total_cop": 90000,
                "flow_token": "shipping_wa_test_2",
            },
        },
    )
    effective = asyncio.run(translate_to_effective_text(msg))
    event = detect_chat_event(_user(effective.text))
    assert event["kind"] == "shipping_form"
    assert event["city"] == "Medellín"
    assert event["address"] == "Cra 1 # 2-3"
    assert event["order_total_cop"] == 90000
    assert event["extra"] == []
