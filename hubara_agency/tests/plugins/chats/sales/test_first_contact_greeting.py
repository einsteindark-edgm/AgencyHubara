"""Saludo determinista en el primer contacto cuando el turno sale por tool.

Incidente 2026-09-10/11 (runs dc32f7fe y 3ce50ef3, ambos
CTWA "amor y amistad"): el LLM escribió "¡Buenas noches! Bienvenido a
*Hubara*..." JUNTO a la tool `search_products` y luego terminó el turno con
`present_products`. El default-deny (run 1c9ef231) descarta el content que
acompaña tool calls → el cliente recibió el menú de productos SIN saludo. En
la run a15bb71c (CTWA "velas aromáticas") el LLM respondió solo
texto y el saludo sí salió.

Contrato: si es el PRIMER contacto de la conversación (sin ningún mensaje
del agente en el historial) y el turno le manda algo al cliente (una tool
presentacional o un texto) sin que nada de lo que recibe lleve el saludo,
el workflow manda la Burbuja 1 del guion de apertura (saludo por hora +
propuesta de valor) ANTES que todo lo demás. Puro y determinista: la
decisión no depende del LLM. Desde 2026-09-29 también en turnos de texto
(caso de Halloween del laboratorio).
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


def test_skips_when_the_text_turn_already_greets() -> None:
    # Texto solo (run a15bb71c): el LLM saludó en su propio texto — nada que
    # inyectar.
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=[],
        client_texts=["¡Buenas tardes! Bienvenido a *Hubara*..."],
    )


def test_sends_when_a_text_turn_does_not_greet() -> None:
    """Caso real del laboratorio (2026-09-28, CTWA de Halloween): el LLM
    saludó JUNTO a `search_products` (el default-deny lo descartó) y cerró
    con un texto sin saludo («Tenemos 4 piezas de la colección…»). La regla
    de antes suponía que en un turno de texto el LLM saluda en su texto: no
    siempre. Primer contacto sin saludo en nada de lo que sale → Burbuja 1."""
    assert should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["search_products"],
        client_texts=["Tenemos 4 piezas de la colección de Halloween, todas con aroma a frutos rojos."],
    )
    assert should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["search_products", "set_order_slot"],
        client_texts=["¿Cuál te gusta más?"],
    )
    assert should_send_first_contact_greeting(
        first_contact=True, tools_used=[], client_texts=["Con gusto te cuento. ¿Buscas algo en particular?"]
    )


def test_in_flight_histories_keep_the_old_rule() -> None:
    """Las histories en vuelo de antes del cambio se reproducen con la regla
    vieja (el workflow la pide con `workflow.patched`): un turno de texto sin
    tool que le escriba al cliente no pedía saludo."""
    assert not should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["search_products"],
        client_texts=["Tenemos 4 piezas de la colección de Halloween."],
        text_turns=False,
    )
    assert should_send_first_contact_greeting(
        first_contact=True,
        tools_used=["present_products"],
        client_texts=["Estas son nuestras piezas:"],
        text_turns=False,
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
