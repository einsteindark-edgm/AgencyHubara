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
    previous_episode_summary,
    quote_template_in_turn,
    unseen_template_text,
    with_previous_episode,
)


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
