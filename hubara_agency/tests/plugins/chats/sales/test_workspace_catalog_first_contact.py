"""El primer contacto que ya dice qué quiere ver no se abre con botones
(incidente del 2026-10-13, anuncio de velitas del 7 de diciembre).

Con `rafaga-v8` Jev leyó «catálogo» 0,97 en «¡Hola! Quiero más información
sobre las velitas del 7 de diciembre.» y el contrato del turno pedía
`present_products`. Pero la apertura del guion ordenaba «Burbuja 2
(`send_quick_replies`)»: el modelo la siguió («¿Buscas las velitas para una
ocasión especial o quieres ver las opciones?» + «Ver catálogo») y, como los
botones TERMINAN el turno, la segunda puerta del contrato no actuó (solo
retiene un turno que cierra con texto). El anuncio de Halloween salía bien
porque el modelo buscó antes de abrir.

El guion lo dice ahora (contrato sobre texto, como
`test_workspace_returning_customer.py`): si la lectura del turno trae
«catálogo», la Burbuja 2 es buscar y mostrar con `present_products`, sin
botones; los botones quedan para el que solo saluda. La bienvenida la pone el
sistema antes de la lista (`first_contact_greeting`).
"""
from __future__ import annotations

from pathlib import Path

WORKSPACE = Path("src/plugins/chats/agent/sales/workspace")


def _opening(text: str) -> str:
    start = text.index("## Apertura")
    return text[start : text.index("\n## ", start + 1)]


def test_a_first_message_that_asks_for_the_catalog_gets_the_products_not_buttons() -> None:
    opening = _opening((WORKSPACE / "skills/etapa_descubrimiento/SKILL.md").read_text(encoding="utf-8"))
    [shows] = [line for line in opening.splitlines() if "«catálogo»" in line]

    assert "search_products" in shows and "present_products" in shows
    assert "NO uses `send_quick_replies`" in shows


def test_the_buttons_are_only_for_who_just_says_hello() -> None:
    opening = _opening((WORKSPACE / "skills/etapa_descubrimiento/SKILL.md").read_text(encoding="utf-8"))
    [buttons] = [line for line in opening.splitlines() if "catalog.browse" in line]

    assert "Solo saluda" in buttons


def test_the_tool_table_says_quick_replies_are_not_for_a_catalog_turn() -> None:
    tools = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    [row] = [line for line in tools.splitlines() if line.startswith("| `send_quick_replies`")]

    assert "«catálogo»" in row and "present_products" in row
