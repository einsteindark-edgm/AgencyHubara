"""Lo que el LLM sabe del episodio anterior cuando empieza uno nuevo.

Run 28a8e407 (2026-09-23): lo anterior viajaba como el `motivo` que el LLM
escribió al cerrar (la Trilogía con un cupón que no era) y exoclaw lo pegaba
delante de CADA mensaje del cliente. Ahora es una línea armada con hechos del
episodio cerrado (cómo cerró, qué pedido, qué productos) y va UNA vez, al
inicio del primer mensaje del episodio nuevo — ahí queda grabada en el
historial del LLM, que arranca limpio.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.episode_memory import (
    handoff_reply_of,
    previous_episode_summary,
    quote_team_exchange_in_turn,
    quote_template_in_turn,
    unseen_team_exchange,
    unseen_template_text,
    with_previous_episode,
)
from src.plugins.chats.shared.team_exchange import our_lines_in_turn


def _cubo_love_order() -> dict:
    return {
        "episode_id": "ep_006",
        "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
        "order_id": "order_01TEST",
        "applied_coupon": {"code": "AMOR26"},
        "order_draft": {
            "items": [
                {
                    "producto": "Cubo Love",
                    "cantidad": "2",
                    "aroma": "Coco cremoso",
                    "color": "Azul",
                }
            ]
        },
    }


def test_order_waiting_for_payment_names_the_order_and_what_was_bought() -> None:
    summary = previous_episode_summary(_cubo_love_order())

    assert "pedido registrado" in summary
    assert "order_01TEST" in summary
    assert "2× Cubo Love (Coco cremoso, Azul)" in summary
    assert "AMOR26" in summary
    assert "equipo" in summary


def test_episode_closed_by_a_campaign_was_an_open_conversation_without_purchase() -> None:
    episode = {
        "closing_tag": "CAMPAIGN_REPLY",
        "order_draft": {
            "slots": {
                "producto": "Trilogía del Terror",
                "cantidad": "1",
                "aroma": "Frutos rojos",
                "color": "Blanco",
                "ciudad": "Bogotá",
            }
        },
    }

    summary = previous_episode_summary(episode)

    assert "sin compra" in summary
    assert "1× Trilogía del Terror (Frutos rojos, Blanco)" in summary
    assert "Bogotá" not in summary


def test_rejection_timeout_and_purchase_have_their_own_outcome() -> None:
    assert "no comprar" in previous_episode_summary({"closing_tag": "RECHAZO"})
    assert "sin respuesta" in previous_episode_summary({"closing_tag": "TIMEOUT"})
    bought = previous_episode_summary(
        {"closing_tag": "COMPRA_EXITOSA", "order_id": "order_02"}
    )
    assert "compra" in bought and "order_02" in bought


def test_without_draft_there_is_no_product_clause() -> None:
    summary = previous_episode_summary({"closing_tag": "RECHAZO"})

    assert "hablaron de" not in summary
    assert "×" not in summary


def test_the_line_goes_once_before_the_customer_text() -> None:
    text = with_previous_episode(_cubo_love_order(), "AMOR26")

    first, rest = text.split("\n", 1)
    assert first.startswith("[Conversación anterior con este cliente")
    assert first.endswith("]")
    assert "no la retomes" in first
    assert rest == "AMOR26"


# --- La plantilla que recibió el cliente (fase 3) -----------------------------
# El LLM no ve las plantillas: se envían por fuera de su historial (van solo
# al JSONL del dashboard, `kind: template`). Si el cliente responde a una, el
# turno la cita — igual que la campaña (run edbb0d8b).

_TEMPLATE = {
    "role": "assistant",
    "kind": "template",
    "content": "Hola 🌿 Te quedó sonando la Trilogía del Terror. ¿La retomamos?",
}


def test_template_right_before_the_reply_is_the_one_to_quote() -> None:
    events = [{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Hola!"}, _TEMPLATE]
    assert unseen_template_text(events) == _TEMPLATE["content"]


def test_nothing_to_quote_when_the_last_message_was_not_a_template() -> None:
    assert unseen_template_text([_TEMPLATE, {"role": "assistant", "content": "¿Te ayudo?"}]) is None
    assert unseen_template_text([_TEMPLATE, {"role": "user", "content": "sí"}]) is None
    assert unseen_template_text([]) is None


def test_template_quote_goes_before_the_customer_text() -> None:
    text = quote_template_in_turn(_TEMPLATE["content"], "Sí, cuéntame")

    first, rest = text.split("\n", 1)
    assert first == (
        "[El cliente responde a este mensaje que le enviamos: "
        "«Hola 🌿 Te quedó sonando la Trilogía del Terror. ¿La retomamos?»]"
    )
    assert rest == "Sí, cuéntame"


# --- Lo que un colega le escribió al cliente (caso del 2026-10-09) ------------
# El LLM tampoco ve lo que escribe el equipo desde el chat: va solo al JSONL
# del dashboard (`sender: human`). El bot dijo que no había descuento para esas
# piezas y ofreció mostrar otra línea; un colega tomó el chat, le escribió que
# sí le aplicaban el de la página y devolvió el chat al bot. El cliente contestó
# «Si por favor» al colega y el bot, que no lo vio, le mandó la línea que él
# mismo había ofrecido.

_BOT_OFFER = {
    "role": "assistant",
    "content": "Estas piezas no tienen promoción. ¿Te muestro la línea que sí la tiene?",
}
_COLLEAGUE = {
    "role": "assistant",
    "sender": "human",
    "content": "Claro que sí, el descuento de la página te lo aplicamos",
}


def test_what_a_colleague_wrote_after_the_bot_is_what_the_bot_did_not_see() -> None:
    events = [{"role": "user", "content": "¿Me aplicas el descuento?"}, _BOT_OFFER, _COLLEAGUE]

    assert unseen_team_exchange(events) == [
        ("colega", "Claro que sí, el descuento de la página te lo aplicamos")
    ]


def test_what_the_customer_answered_the_colleague_goes_too_in_order() -> None:
    events = [
        _BOT_OFFER,
        _COLLEAGUE,
        {"role": "user", "content": "¿Y el envío?"},
        {"role": "assistant", "sender": "human", "kind": "template", "content": "El envío va aparte"},
    ]

    assert unseen_team_exchange(events) == [
        ("colega", "Claro que sí, el descuento de la página te lo aplicamos"),
        ("cliente", "¿Y el envío?"),
        ("colega", "El envío va aparte"),
    ]


def test_an_automatic_notice_between_them_is_part_of_what_the_bot_did_not_see() -> None:
    notice = {"role": "assistant", "kind": "template", "content": "Tu pedido #47 ya está listo"}

    assert unseen_team_exchange([_BOT_OFFER, notice, _COLLEAGUE]) == [
        ("aviso", "Tu pedido #47 ya está listo"),
        ("colega", "Claro que sí, el descuento de la página te lo aplicamos"),
    ]


def test_nothing_to_quote_without_a_colleague_after_the_last_bot_message() -> None:
    # Lo que el colega escribió ANTES de la última respuesta del bot ya llegó
    # citado en ese turno; sin colega no hay nada nuevo (la plantilla sola
    # tiene su propia cita).
    assert unseen_team_exchange([_COLLEAGUE, _BOT_OFFER]) is None
    assert unseen_team_exchange([_BOT_OFFER, {"role": "user", "content": "ok"}]) is None
    assert unseen_team_exchange([_BOT_OFFER, _TEMPLATE]) is None
    assert unseen_team_exchange([]) is None


def test_a_card_the_bot_sent_is_its_own_message() -> None:
    card = {"role": "assistant", "kind": "ui_component", "content": "🛍️ El bot envió el catálogo con 4 productos"}

    assert unseen_team_exchange([_COLLEAGUE, card]) is None


def test_a_photo_from_the_colleague_without_caption_still_counts() -> None:
    photo = {"role": "assistant", "sender": "human", "content": "", "image_url": "media/out/x.jpg"}

    assert unseen_team_exchange([_BOT_OFFER, photo]) == [("colega", "(una foto)")]


def test_a_long_exchange_keeps_its_last_lines() -> None:
    events = [_BOT_OFFER] + [
        {"role": "assistant", "sender": "human", "content": f"mensaje {k}"} for k in range(20)
    ]

    exchange = unseen_team_exchange(events)

    assert exchange is not None and len(exchange) == 8
    assert exchange[-1] == ("colega", "mensaje 19")


def test_the_exchange_goes_before_the_customer_text_in_a_single_note() -> None:
    text = quote_team_exchange_in_turn(
        [("colega", "Claro que sí [el de la web]"), ("cliente", "¿y el envío?")],
        "Si por favor",
    )

    note, rest = text.rsplit("\n", 1)
    assert rest == "Si por favor"
    # Una sola nota entre corchetes: la calificación quita las notas del ingest
    # hasta el primer «]» (`_INGEST_NOTE_RE`).
    assert note.startswith("[") and note.endswith("]") and note.count("]") == 1
    assert "colega del equipo" in note
    assert "«Claro que sí (el de la web)»" in note
    assert "«¿y el envío?»" in note
    assert "no contradigas" in note


# --- El gancho de remarketing tampoco lo vio Ventas (caso del 2026-10-16) -----
# Un colega corrigió el precio (la caja de 12, no una unidad), devolvió el chat
# y remarketing mandó un gancho. El gancho se guardaba igual que un mensaje de
# Ventas: la nota se cortaba ahí y Ventas no supo nada de lo que dijo el colega.

_HOOK = {
    "role": "assistant",
    "agent": "remarketing",
    "content": "Hola de nuevo, quedó pendiente lo de la caja de 12. ¿Te ayudo a cerrar el pedido?",
}


def test_the_remarketing_hook_is_not_a_message_the_bot_saw() -> None:
    events = [
        _BOT_OFFER,
        _COLLEAGUE,
        {"role": "user", "content": "¿Entonces cada una vale eso?"},
        {"role": "assistant", "sender": "human", "content": "Es la caja de 12 por ese precio"},
        _HOOK,
    ]

    assert unseen_team_exchange(events) == [
        ("colega", "Claro que sí, el descuento de la página te lo aplicamos"),
        ("cliente", "¿Entonces cada una vale eso?"),
        ("colega", "Es la caja de 12 por ese precio"),
        ("aviso", "Hola de nuevo, quedó pendiente lo de la caja de 12. ¿Te ayudo a cerrar el pedido?"),
    ]


def test_a_hook_alone_still_needs_a_colleague_to_be_quoted_at_the_ingest() -> None:
    # Sin colega, el ingest no cambia: el gancho lo maneja remarketing.
    assert unseen_team_exchange([_BOT_OFFER, _HOOK]) is None


# --- El turno del traspaso de remarketing a Ventas (caso del 2026-10-16) ------
# Ventas solo recibía el resumen que escribió remarketing: arrancó sin el «Si»
# del cliente, sin lo que dijo el colega y sin el gancho, y contestó NO_MESSAGE.
# El turno ahora lleva lo que escribió el cliente, tal cual, con la nota de lo
# que pasó después del último mensaje de Ventas.


def _customer(text: str, wamid: str) -> dict:
    return {"role": "user", "content": text, "wamid": wamid, "timestamp": "2026-10-16T21:09:01.034010+00:00"}


def test_the_handoff_turn_is_what_the_customer_wrote_after_what_ventas_did_not_see() -> None:
    events = [
        _BOT_OFFER,
        _COLLEAGUE,
        {"role": "user", "content": "¿Entonces cada una vale eso?"},
        {"role": "assistant", "sender": "human", "content": "Es la caja de 12 por ese precio"},
        _HOOK,
        _customer("Si", "wamid.in9"),
    ]

    reply = handoff_reply_of(events)

    assert reply is not None
    note, said = reply.text.rsplit("\n", 1)
    assert said == "Si"
    assert "colega del equipo" in note
    assert "«Es la caja de 12 por ese precio»" in note
    assert "«¿Entonces cada una vale eso?»" in note
    assert "«Hola de nuevo, quedó pendiente lo de la caja de 12. ¿Te ayudo a cerrar el pedido?»" in note
    assert reply.customer_text == "Si"
    assert reply.wamids == ["wamid.in9"]
    assert reply.ts_ms == 1792184941034


def test_only_the_hook_before_the_reply_gets_its_own_note() -> None:
    reply = handoff_reply_of([_BOT_OFFER, _HOOK, _customer("Sí, me interesa", "wamid.in1")])

    assert reply is not None
    note, said = reply.text.rsplit("\n", 1)
    assert said == "Sí, me interesa"
    assert note.startswith("[") and note.endswith("]") and note.count("]") == 1
    assert "colega" not in note
    assert "«Hola de nuevo, quedó pendiente lo de la caja de 12. ¿Te ayudo a cerrar el pedido?»" in note


def test_several_messages_of_the_customer_go_together() -> None:
    reply = handoff_reply_of([_BOT_OFFER, _HOOK, _customer("Si", "w1"), _customer("¿y el envío?", "w2")])

    assert reply is not None
    assert reply.text.endswith("\nSi\n¿y el envío?")
    assert reply.customer_text == "Si\n¿y el envío?"
    assert reply.wamids == ["w1", "w2"]


def test_what_the_customer_wrote_goes_whole_even_long_and_in_lines() -> None:
    # Después del traspaso vienen los datos de envío: una dirección larga y en
    # varias líneas no se recorta ni se aplana (la nota sí recorta SUS líneas).
    address = "Ana Pérez\nCalle 59b sur # 38-12, apto 301, torre 4, conjunto " + "Los Pinos " * 30 + "\nPago contraentrega"
    reply = handoff_reply_of([_BOT_OFFER, _HOOK, _customer(address, "w1")])

    assert reply is not None
    assert reply.customer_text == address
    assert reply.text.endswith("\n" + address)


def test_a_photo_without_caption_still_counts_as_what_the_customer_wrote() -> None:
    photo = {"role": "user", "content": "", "image_url": "media/in/x.jpg", "wamid": "w1"}

    reply = handoff_reply_of([_BOT_OFFER, _HOOK, photo])

    assert reply is not None
    assert reply.customer_text == "(una foto)"


def test_without_anything_ventas_missed_the_turn_is_just_the_customer() -> None:
    reply = handoff_reply_of([_BOT_OFFER, _customer("Si", "w1")])

    assert reply is not None
    assert reply.text == "Si"


def test_no_handoff_turn_when_the_customer_did_not_write_last() -> None:
    assert handoff_reply_of([_BOT_OFFER, _HOOK]) is None
    assert handoff_reply_of([_BOT_OFFER, _customer("Si", "w1"), _COLLEAGUE]) is None
    assert handoff_reply_of([_BOT_OFFER]) is None
    assert handoff_reply_of([]) is None


def test_a_message_that_already_reached_ventas_does_not_go_twice() -> None:
    events = [_BOT_OFFER, _HOOK, _customer("Si", "w1"), _customer("¿y el envío?", "w2")]

    reply = handoff_reply_of(events, already_pending=["w2"])

    assert reply is not None
    assert reply.customer_text == "Si"
    assert reply.wamids == ["w1"]
    assert handoff_reply_of(events, already_pending=["w1", "w2"]) is None


def test_the_grading_reads_the_hook_of_the_note_as_ours() -> None:
    # La calificación no le cobra al bot repetir lo que dijimos nosotros.
    reply = handoff_reply_of([_BOT_OFFER, _HOOK, _customer("Si", "w1")])

    assert reply is not None
    assert our_lines_in_turn(reply.text) == [
        "Hola de nuevo, quedó pendiente lo de la caja de 12. ¿Te ayudo a cerrar el pedido?"
    ]
