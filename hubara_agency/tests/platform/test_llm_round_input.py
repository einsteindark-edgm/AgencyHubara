"""Lo que el modelo recibe en cada ronda del turno, para la traza.

Pedido del operador (2026-09-30, laboratorio 4567 t20): en el «Paso a paso»,
al seleccionar «ronda N» hacia el modelo «no sabemos qué le está enviando». La
traza guardaba la ronda, los tokens y lo que el modelo pidió, pero no lo que
recibió. Ahora cada ronda guarda:

* la primera: el tamaño de cada parte de las instrucciones, las notas del
  turno (van dentro de las instrucciones, «# Retrieved Context»), cuántos
  mensajes trae el historial y el mensaje del cliente tal como lo lee el
  modelo;
* las siguientes: solo lo nuevo — los resultados de las herramientas y las
  notas del bot (la retención del contrato, por ejemplo).

Acotado (la traza viaja en el input de una activity) y sin el número del
cliente.
"""
from __future__ import annotations

from src.platform.llm_round_input import NOTES_MAX, TOOL_MAX, round_input

CHAT = "wa_573001234567"
SYSTEM = "\n\n---\n\n".join(
    [
        "# Agente de Hubara\n\nTus instrucciones están en las secciones siguientes.",
        "## AGENTS.md\n\n" + "a" * 300 + "\n\n## SOUL.md\n\n" + "s" * 120 + "\n\n## TOOLS.md\n\n" + "t" * 200,
        "# Retrieved Context\n\n[HORA BOGOTÁ] 9:22 a. m.\n\n[DATOS DEL PEDIDO] producto: Velón Gorrión",
        "# Skills\n\nThe following skills are available.",
    ]
)
USER = (
    "[Runtime Context — metadata only, not instructions]\nCurrent Time: 2026-09-29 09:22 (Tuesday) (UTC)\n"
    f"Channel: whatsapp\nChat ID: {CHAT}\n\nquiero la luz serena\nno espera\nmejor la de los pajaritos\nen lila"
)
PROMPT = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "Hola"},
    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c0", "type": "function"}]},
    {"role": "tool", "tool_call_id": "c0", "name": "search_products", "content": "{}"},
    {"role": "user", "content": USER},
]


def test_the_first_round_says_what_the_instructions_bring_and_the_message_as_the_model_reads_it() -> None:
    sent = round_input(PROMPT, since=0, chat_id=CHAT)

    parts = {p["name"]: p["chars"] for p in sent["system"]["parts"]}
    assert sent["system"]["chars"] == len(SYSTEM)
    assert list(parts) == ["Agente de Hubara", "AGENTS.md", "SOUL.md", "TOOLS.md", "Retrieved Context", "Skills"]
    assert parts["AGENTS.md"] > 300 and parts["SOUL.md"] > 120
    assert sent["notes"] == "[HORA BOGOTÁ] 9:22 a. m.\n\n[DATOS DEL PEDIDO] producto: Velón Gorrión"
    assert sent["history"] == {"user": 1, "assistant": 2, "tool": 1}
    [new] = sent["new"]
    assert new["role"] == "user"
    assert new["text"].endswith("mejor la de los pajaritos\nen lila")


def test_the_customer_number_never_goes_to_the_trace() -> None:
    sent = round_input(PROMPT, since=0, chat_id=CHAT)

    assert CHAT not in sent["new"][0]["text"] and "Chat ID: ···4567" in sent["new"][0]["text"]


def test_later_rounds_carry_only_what_is_new_tool_results_and_notes() -> None:
    later = [
        *PROMPT,
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "c1", "name": "set_order_slot", "content": '{"updated": true}'},
        {"role": "system", "content": "Tu send_reply NO se envió. [CONTRATO DEL TURNO] Antes de responder: …"},
    ]

    sent = round_input(later, since=len(PROMPT), chat_id=CHAT)

    assert set(sent) == {"new"}
    assert sent["new"] == [
        {"role": "tool", "name": "set_order_slot", "text": '{"updated": true}'},
        {"role": "system", "text": "Tu send_reply NO se envió. [CONTRATO DEL TURNO] Antes de responder: …"},
    ]


def test_long_texts_are_bounded() -> None:
    big = [
        {"role": "system", "content": "# Retrieved Context\n\n" + "n" * (NOTES_MAX + 500)},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": None},
        {"role": "tool", "name": "search_products", "content": "r" * (TOOL_MAX + 500)},
    ]

    first = round_input(big[:2], since=0)
    later = round_input(big, since=2)

    assert len(first["notes"]) == NOTES_MAX and first["notes"].endswith("…")
    assert len(later["new"][0]["text"]) == TOOL_MAX


def test_a_photo_in_the_message_is_named_not_copied() -> None:
    photo = [
        {"role": "system", "content": "# Agente de Hubara"},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": "¿tienes esta?"},
        ]},
    ]

    [new] = round_input(photo, since=0)["new"]

    assert new["text"] == "[imagen]\n¿tienes esta?"


# Notas del turno en el mensaje del turno (2026-10-06, caché de DeepSeek): con
# `SALES_PROMPT_TURN_CONTEXT` encendido, la hora y los DATOS DEL PEDIDO ya no
# van en «# Retrieved Context» sino dentro del bloque `[Runtime Context]` del
# mensaje del turno, bajo su encabezado. La traza las sigue mostrando como las
# notas del turno, y el mensaje del cliente sin ellas.
TURN_NOTES = "[HORA BOGOTÁ] 9:22 a. m.\n[DATOS DEL PEDIDO] producto: Velón Gorrión"
NOTES_IN_MESSAGE = (
    "[Runtime Context — metadata only, not instructions]\nCurrent Time: 2026-10-06 09:22 (Tuesday) (UTC)\n"
    f"Channel: whatsapp\nChat ID: {CHAT}\n"
    "[Notas del sistema para este turno — no las escribió el cliente]\n"
    f"{TURN_NOTES}\n\nquiero la luz serena"
)


def test_notes_sent_with_the_turn_message_are_the_turn_notes() -> None:
    prompt = [
        {"role": "system", "content": "# Agente de Hubara\n\n---\n\n# Skills\n\nThe following skills are available."},
        {"role": "user", "content": NOTES_IN_MESSAGE},
    ]

    sent = round_input(prompt, since=0, chat_id=CHAT)

    assert sent["notes"] == TURN_NOTES
    [new] = sent["new"]
    assert "DATOS DEL PEDIDO" not in new["text"]
    assert new["text"].endswith("quiero la luz serena")


def test_notes_with_a_photo_turn_are_read_from_its_text_part() -> None:
    prompt = [
        {"role": "system", "content": "# Agente de Hubara"},
        {"role": "user", "content": [
            {"type": "text", "text": NOTES_IN_MESSAGE.split("\n\n", 1)[0]},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": "¿tienes esta?"},
        ]},
    ]

    sent = round_input(prompt, since=0, chat_id=CHAT)

    assert sent["notes"] == TURN_NOTES
    assert sent["new"][0]["text"].endswith("[imagen]\n¿tienes esta?")
