"""Reloj del turno original en el sandbox (plan §3.6, PR 11): el saludo por
hora, el bloque de hora de Bogotá y el "Current Time" del prompt de exoclaw
salen a la hora del turno REAL; al salir vuelve el reloj real."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.plugins.chats.agent.sales_lab.sandbox.clock import frozen_clock

BOGOTA = ZoneInfo("America/Bogota")
AT_MS = int(datetime(2026, 9, 23, 8, 15, tzinfo=BOGOTA).timestamp() * 1000)


def test_frozen_clock_puts_the_turn_at_its_original_hour() -> None:
    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.plugins.chats.agent.sales.first_contact_greeting import build_first_contact_greeting

    original_at = datetime(2026, 9, 23, 8, 15, tzinfo=BOGOTA)
    with frozen_clock(AT_MS):
        greeting = build_first_contact_greeting()
        context = build_bogota_context_string()
        import exoclaw_conversation.context as exo

        prompt_now = exo.datetime.now()

    assert greeting == build_first_contact_greeting(now=original_at)
    assert context == build_bogota_context_string(now=original_at)
    assert abs(prompt_now.timestamp() * 1000 - AT_MS) < 5_000
    assert build_first_contact_greeting() == build_first_contact_greeting(now=datetime.now(BOGOTA))


def test_frozen_clock_restores_the_real_clock() -> None:
    import exoclaw_conversation.context as exo

    before = exo.datetime
    with frozen_clock(AT_MS):
        assert exo.datetime is not before
    assert exo.datetime is before
