"""Los avisos que el bot no escribió llegan al turno siguiente (premortem C5).

Reseñas tras la entrega (2026-10-13): la invitación a calificar (Flow) y el
gracias con el cupón (botón a Google) salen por fuera del bot. Quedaban en el
chat como si fueran del bot, así que el turno siguiente daba por hecho que el
LLM ya los tenía: el cliente escribía «quiero otra igual» y el bot no sabía
que le habíamos dado un cupón. Ahora quedan marcados (`kind: notice`) y el
turno los cita, sin romper el acuse («¡Gracias! 🙏» al gracias sigue sin abrir
conversación).
"""
from __future__ import annotations

import time

import pytest

from src.plugins.chats.agent.sales.use_cases.episode_memory import (
    quote_notices_in_turn,
    unseen_notices,
    unseen_team_exchange,
)
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.plugins.chats.test_campaign_reply import _SESSION, _Loader, _message, _Store
from tests.plugins.chats.test_closing_ack import _History

_BOT = {"role": "assistant", "content": "¡Gracias por tu compra! 🤍"}
_INVITE = {"role": "assistant", "kind": "notice", "sender": "reviews",
           "content": "¿Qué tal tu pedido #31?\n[Calificar]"}
_FORM = {"role": "user", "content": "[calificación recibida] ★★★★★ (5/5)"}
_THANKS = {"role": "assistant", "kind": "notice", "sender": "reviews",
           "content": "¡Gracias! Tu cupón GRACIAS5 queda listo.\n[Opinar en Google] https://x.co/opinar/"}


def test_notices_after_the_last_bot_message_are_unseen():
    # En una línea cada uno (van dentro de la nota del turno).
    assert unseen_notices([_BOT, _INVITE, _FORM, _THANKS]) == [
        "¿Qué tal tu pedido #31? [Calificar]",
        "¡Gracias! Tu cupón GRACIAS5 queda listo. [Opinar en Google] https://x.co/opinar/",
    ]


def test_without_notices_or_with_the_bot_after_them_there_is_nothing_to_quote():
    assert unseen_notices([_BOT]) is None
    assert unseen_notices([_INVITE, _BOT]) is None
    assert unseen_notices([]) is None


def test_the_quote_has_no_brackets_inside():
    quoted = quote_notices_in_turn([_THANKS["content"]], "Hola")
    note, _, text = quoted.partition("]\n")
    assert text == "Hola"
    assert "[" not in note[1:] and "GRACIAS5" in note and "(Opinar en Google)" in note


def test_a_colleague_message_after_a_notice_still_shows_the_notice_as_ours():
    colleague = {"role": "assistant", "sender": "human", "content": "Te escribo del equipo"}
    lines = unseen_team_exchange([_BOT, _THANKS, colleague])
    assert lines is not None and ("aviso", _THANKS["content"].replace("\n", " ")) in lines


@pytest.mark.asyncio
async def test_the_next_turn_quotes_the_thanks_notice():
    now = int(time.time() * 1000)
    store = _Store({
        "active_route": "ventas", "last_inbound_at_ms": now - 60_000,
        "episodes": [{"episode_id": "ep_002", "started_at_ms": now - 60_000, "closed_at_ms": None}],
    })
    loader = _Loader()
    use_case = IngestInboundMessage(
        history_store=_History([_BOT, _THANKS]),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )

    await use_case.execute(_message("Quiero otra igual"))

    [call] = loader.calls
    assert "GRACIAS5" in call["message"] and call["message"].endswith("Quiero otra igual")
    assert _SESSION in store.data
