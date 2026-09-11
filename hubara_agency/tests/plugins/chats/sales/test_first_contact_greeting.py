"""Saludo determinista en el primer contacto cuando el turno sale por tool.

Incidente 2026-09-10/11 (runs dc32f7fe y 3ce50ef3, ambos
CTWA "amor y amistad"): el LLM escribió "¡Buenas noches! Bienvenido a
*Hubara*..." JUNTO a la tool `search_products` y luego terminó el turno con
`present_products`. El default-deny (run 1c9ef231) descarta el content que
acompaña tool calls → el cliente recibió el menú de productos SIN saludo. En
la run a15bb71c (CTWA "velas aromáticas") el LLM respondió solo
texto y el saludo sí salió.

Contrato: si es el PRIMER contacto de la conversación (sin ningún mensaje
del agente en el historial) y el turno toca al cliente vía una tool
presentacional sin que ninguna burbuja de texto lleve el saludo, el
workflow manda la Burbuja 1 del guion de apertura (saludo por hora +
propuesta de valor) ANTES de flushear el menú. Puro y determinista: la
decisión no depende del LLM.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.plugins.chats.agent.sales.first_contact_greeting import (
    build_first_contact_greeting,
    should_send_first_contact_greeting,
)

_BOGOTA = ZoneInfo("America/Bogota")


def test_greeting_follows_bogota_hour_and_opening_script() -> None:
    night = datetime(2026, 9, 10, 22, 38, tzinfo=_BOGOTA)
    assert build_first_contact_greeting(night) == (
        "¡Buenas noches! Bienvenido a *Hubara*, velas artesanales hechas a "
        "base de cera de palma, a mano en Colombia."
    )
    morning = datetime(2026, 9, 10, 8, 0, tzinfo=_BOGOTA)
    assert build_first_contact_greeting(morning).startswith("¡Buenos días!")
    afternoon = datetime(2026, 9, 10, 12, 9, tzinfo=_BOGOTA)
    assert build_first_contact_greeting(afternoon).startswith("¡Buenas tardes!")


def test_greeting_uses_bogota_hour_when_given_utc() -> None:
    # 03:38 UTC del 11/09 = 22:38 del 10/09 en Bogotá → "Buenas noches".
    utc = datetime(2026, 9, 11, 3, 38, tzinfo=ZoneInfo("UTC"))
    assert build_first_contact_greeting(utc).startswith("¡Buenas noches!")


def test_sends_when_first_contact_exits_via_catalog_without_text() -> None:
    # El caso real: search_products + present_products, sin burbuja de texto,
    # intro_text sin saludo.
    assert should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["search_products", "present_products"],
        client_texts=["Estas son nuestras piezas para amor y amistad:"],
    )


def test_sends_when_greeting_was_dropped_next_to_quick_replies() -> None:
    assert should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["send_quick_replies"],
        client_texts=["¿Es para ti o para regalo?"],
    )


def test_skips_when_not_first_contact() -> None:
    assert not should_send_first_contact_greeting(
        first_contact=False,
        tools_used=["search_products", "present_products"],
        client_texts=["Estas son nuestras piezas para amor y amistad:"],
    )


def test_skips_when_turn_has_no_outbound_tool() -> None:
    # Texto solo (run a15bb71c): el LLM saluda en su propio texto y el
    # workflow lo manda como siempre — nada que inyectar.
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=[],
        client_texts=["¡Buenas tardes! Bienvenido a *Hubara*..."],
    )
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["search_products", "set_order_slot"],
        client_texts=["¿Cuál te gusta más?"],
    )


def test_skips_when_some_client_text_already_greets() -> None:
    # Saludo en el intro_text de la tool (el canal legítimo) → no duplicar.
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["present_products"],
        client_texts=["¡Buenas noches! Estas son nuestras piezas:"],
    )
    # Sin tilde y en otra posición también cuenta.
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["present_products"],
        client_texts=["Bienvenido a Hubara. Buenos dias, estas son:"],
    )
    # Saludo en una burbuja de texto que sale antes del flush.
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["present_product_detail"],
        client_texts=["Hola, mira esta pieza:", ""],
    )


def test_skips_when_tools_used_is_empty_even_if_first_contact() -> None:
    assert not should_send_first_contact_greeting(
        first_contact=True, tools_used=[], client_texts=[]
    )
