"""La nota de la segunda puerta (`contract_policy_of`) según lo que falta.

Turno 1 de …7392 (2026-10-08): el contrato pedía el catálogo
(`present_products`) y el modelo contestó con un texto que listaba los
productos. La nota terminaba con «No le escribas al cliente hasta tener el
dato de la herramienta»: el modelo ya tenía el dato (de `search_products`) y
volvió a escribir el mismo texto. Esa frase sirve cuando falta un DATO (el
precio, las medidas); cuando falta una herramienta que le MUESTRA algo al
cliente, la nota dice que el texto solo no basta.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.contracts import TurnDecisions
from src.plugins.chats.agent.sales.decisions.facade import contract_policy_of

DATA_TAIL = "No le escribas al cliente hasta tener el dato de la herramienta."
SHOW_TAIL = "Esa herramienta es la que se lo muestra al cliente: llámala en esta misma respuesta, tu texto solo no basta."

CATALOGO = {
    "topic": "catalogo",
    "any_of": ["present_products", "present_product_gallery"],
    "nudge": "Para mostrarle el catálogo usa present_products.",
}
PRECIO = {
    "topic": "precio",
    "any_of": ["search_products", "get_product_by_handle", "present_product_detail", "present_products"],
    "nudge": "El precio sale del catálogo: consúltalo con search_products o get_product_by_handle antes de darlo.",
}


def _note(rows: list[dict], tools_used: list[str]) -> str | None:
    decisions = TurnDecisions(ok=True, profile="jev-v5", tools={"required": rows})
    policy = contract_policy_of(decisions)
    assert policy is not None and policy.final_round_note is not None
    return policy.final_round_note(tools_used, "Tenemos 4 piezas de la colección de Halloween")


def test_a_missing_tool_that_shows_something_says_the_text_alone_is_not_enough() -> None:
    note = _note([CATALOGO], ["search_products"])

    assert note == f"[CONTRATO DEL TURNO] Antes de responder: {CATALOGO['nudge']} {SHOW_TAIL}"


def test_a_missing_data_tool_still_asks_to_wait_for_the_data() -> None:
    note = _note([PRECIO], [])

    assert note == f"[CONTRATO DEL TURNO] Antes de responder: {PRECIO['nudge']} {DATA_TAIL}"


def test_with_both_missing_the_note_says_both() -> None:
    note = _note([CATALOGO, PRECIO], [])

    assert note is not None
    assert note.endswith(f"{DATA_TAIL} {SHOW_TAIL}")


def test_a_row_that_accepts_a_read_counts_as_data() -> None:
    """`list_categories` solo le devuelve la lista al modelo (no le muestra
    nada al cliente): una fila que la acepta pide un dato."""
    row = {**CATALOGO, "any_of": [*CATALOGO["any_of"], "list_categories"]}

    assert _note([row], []) == f"[CONTRATO DEL TURNO] Antes de responder: {CATALOGO['nudge']} {DATA_TAIL}"
