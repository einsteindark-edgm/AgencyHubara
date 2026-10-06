"""Guardas de `prompt_layout`: las notas del turno dentro del bloque
`[Runtime Context]` del mensaje del turno (caché de DeepSeek, 2026-10-06).

El recorte al grabar el historial lo hace exoclaw: si cambia su encabezado, o
si una nota trae una línea en blanco, las notas quedarían en el historial.
"""
from __future__ import annotations

from exoclaw_conversation import context as exoclaw_context
from exoclaw_conversation import conversation as exoclaw_conversation

from src.platform.prompt_layout import (
    RUNTIME_CONTEXT_TAG,
    notes_into_turn_message,
    turn_notes,
)

TURN = f"{RUNTIME_CONTEXT_TAG}\nCurrent Time: 2026-10-06 10:22 (Tuesday) (UTC)\n\nquiero el difusor"


def test_the_tag_is_the_one_exoclaw_writes_and_strips() -> None:
    assert exoclaw_context._RUNTIME_CONTEXT_TAG == RUNTIME_CONTEXT_TAG
    assert exoclaw_conversation._RUNTIME_CONTEXT_TAG == RUNTIME_CONTEXT_TAG


def test_blank_lines_inside_a_note_never_split_the_runtime_block() -> None:
    """exoclaw recorta hasta la primera línea en blanco: una nota con párrafos
    dejaría su segunda mitad en el historial."""
    [turn] = notes_into_turn_message(
        [{"role": "user", "content": TURN}], ["[DATOS DEL PEDIDO]\n\nproducto: Velón", "\n[HORA]  10:22\n\n"]
    )

    head, _, rest = turn["content"].partition("\n\n")
    assert rest == "quiero el difusor"
    assert "producto: Velón" in head and "[HORA]  10:22" in head


def test_notes_come_back_out_exactly() -> None:
    [turn] = notes_into_turn_message([{"role": "user", "content": TURN}], ["[A] uno", "[B] dos"])

    notes, content = turn_notes(turn["content"])

    assert notes == "[A] uno\n[B] dos"
    assert content == TURN


def test_without_the_runtime_block_there_is_nowhere_to_put_them() -> None:
    assert notes_into_turn_message([{"role": "user", "content": "hola"}], ["[A] uno"]) is None
    assert notes_into_turn_message([{"role": "system", "content": "x"}], ["[A] uno"]) is None


def test_without_notes_the_messages_are_unchanged() -> None:
    messages = [{"role": "user", "content": TURN}]
    assert notes_into_turn_message(messages, []) == messages
    assert notes_into_turn_message(messages, None) == messages
