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
hablado con la tienda; si el historial empieza con «[Conversación anterior…]»,
se saluda según la hora y se pregunta en qué ayudar, sin «Bienvenido a
*Hubara*» ni la propuesta de valor.
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
    assert "saluda según la hora" in returning and "pregunta en qué" in returning
    assert "sin «Bienvenido a *Hubara*» ni la propuesta de valor" in returning
