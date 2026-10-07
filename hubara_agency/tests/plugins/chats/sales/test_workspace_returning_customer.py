"""El cliente que vuelve no es un primer contacto (incidente del 2026-10-06).

Un cliente volvió 11 días después («Buenas») y en el turno 2 el bot le dio la
bienvenida de marca («Bienvenido a *Hubara*…»): falló APE-04 («No vuelve a
saludar a un cliente con historial»). El guion ataba la bienvenida al «primer
contacto», ambiguo para quien vuelve: al empezar un episodio nuevo el
historial del LLM se corta y su primer mensaje llega con «[Conversación
anterior con este cliente, ya cerrada: …]» adelante (`episode_memory`), así
que el historial PARECE el de alguien nuevo.

El guion lo dice ahora (contrato sobre texto, como
`test_workspace_price_rules.py`): primer contacto = el cliente nunca había
hablado con la tienda. Si el historial empieza con «[Conversación
anterior…]», lo que NO se hace (la bienvenida de marca, la propuesta de
valor) y la nota del turno manda: ese marcador también va en la respuesta a
una campaña («No vuelvas a saludar como si fuera un contacto nuevo») y en la
cortesía («no preguntes en qué más puedes ayudar»). Una instrucción positiva
del guion («saluda y pregunta en qué ayudar») las contradecía (revisión del
PR #390).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.use_cases.episode_memory import with_previous_episode

WORKSPACE = Path("src/plugins/chats/agent/sales/workspace")
#: El encabezado que el ingest pone en el primer mensaje del episodio nuevo.
RETURNING = "[Conversación anterior"
#: Lo que define el primer contacto en el guion.
FIRST_CONTACT = "el cliente nunca había hablado con la tienda"


def test_the_marker_the_script_names_is_the_one_the_ingest_writes() -> None:
    first = with_previous_episode({"closing_tag": "TIMEOUT"}, "Buenas")

    assert first.startswith(RETURNING)


@pytest.mark.parametrize("rel", ["SOUL.md", "skills/etapa_descubrimiento/SKILL.md"])
def test_the_brand_welcome_is_only_for_who_never_talked_with_the_store(rel: str) -> None:
    text = (WORKSPACE / rel).read_text(encoding="utf-8")
    returning = next(line for line in text.splitlines() if RETURNING in line)

    assert FIRST_CONTACT in text
    assert "sin «Bienvenido a *Hubara*» ni la propuesta de valor" in returning
    assert "si la nota del turno dice qué hacer, síguela" in returning


@pytest.mark.parametrize("rel", ["SOUL.md", "skills/etapa_descubrimiento/SKILL.md"])
def test_the_returning_customer_line_does_not_contradict_the_turn_notes(rel: str) -> None:
    """La nota de la campaña dice «No vuelvas a saludar» y la de cortesía «no
    preguntes en qué más puedes ayudar»: la línea del cliente que vuelve no
    ordena saludar ni preguntar en qué ayudar."""
    text = (WORKSPACE / rel).read_text(encoding="utf-8")
    returning = next(line for line in text.splitlines() if RETURNING in line).lower()

    assert "saluda según la hora" not in returning
    assert "pregunta en qué" not in returning
