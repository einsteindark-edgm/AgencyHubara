"""Acuse de despedida tras un cierre (run 4cb3a34f, 2026-09-25).

15:53 Bogotá: el cliente respondió a la campaña de Amor y Amistad "Quería
moldes y ya conseguí, pero gracias por su amabilidad. Éxitos". Ventas cerró
bien: RECHAZO con despedida ("…aquí estamos para ayudarte. ¡Éxitos para ti
también!"). 37 s después el cliente mandó "☺️👍" y el ingest lo trató como
una intención nueva: abrió ep_003 con la nota "saluda con calidez y pregunta
en qué puedes ayudar hoy", cortó el historial del LLM y el bot respondió
"Buenas tardes, bienvenido a *Hubara*… ¿en qué te puedo ayudar hoy?".
Después el ghosting cerró ep_003 como RECHAZO y se evaluó un episodio que no
existió.

Contrato: si el último episodio ya está cerrado (el agente se despidió) y el
cliente solo acusa recibo — emojis, una reacción, un sticker, "gracias", "ok",
"igualmente" — el mensaje queda en el chat pero no abre episodio ni despierta
al agente. Un saludo, una pregunta o cualquier pedido sí abren conversación
nueva, y una respuesta a una campaña o a una plantilla sigue su camino.
"""
from __future__ import annotations

import time

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.closing_ack import is_closing_ack
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from tests.plugins.chats.test_campaign_reply import (
    _SESSION,
    _Loader,
    _message,
    _Store,
    _touch,
)

_FAREWELL = (
    "Con gusto, muchas gracias a ti por escribirnos 🤍. Si más adelante "
    "quieres consentir a alguien con una de nuestras velas, aquí estamos para "
    "ayudarte. ¡Éxitos para ti también!"
)


# --- is_closing_ack ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "☺️👍",
        "🙏",
        "Gracias",
        "Muchas gracias 🙏",
        "ok",
        "Ok, gracias",
        "Igualmente",
        "Bendiciones",
        "Listo, gracias",
        "Gracias, buenas noches",
        "Con gusto",
        "A ti también",
        "Feliz tarde",
        "Chao",
    ],
)
def test_thanks_emojis_and_goodbyes_are_an_ack(text: str) -> None:
    assert is_closing_ack(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Hola",
        "Buenas tardes",
        # Un sí o un dale le contesta algo al bot: no es solo un acuse.
        "Sí",
        "Dale",
        "Claro",
        "No",
        "¿Tienen moldes?",
        "Gracias, ¿tienen moldes?",
        "Gracias pero quiero ver el catálogo",
        "Quiero el cubo love",
        "👍?",
    ],
)
def test_greetings_questions_and_requests_are_not_an_ack(text: str | None) -> None:
    assert not is_closing_ack(text)


# --- ingest ------------------------------------------------------------------


class _History:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = list(events or [])

    def read_events(self, session_id):
        return list(self.events)

    def append_user_event(self, session_id, content, **_kw) -> None:
        self.events.append({"role": "user", "content": content})


def _after_farewell(now_ms: int, *, closed_ago_ms: int = 37_000) -> dict:
    """ep_002 recién cerrado por Ventas con RECHAZO y despedida."""
    return {
        "active_route": "ventas",
        "tag": "RECHAZO",
        "motivo": "Buscaba moldes y ya los consiguió.",
        "last_inbound_at_ms": now_ms - closed_ago_ms - 5_000,
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": now_ms - 10 * 24 * 3600 * 1000,
                "closed_at_ms": now_ms - 9 * 24 * 3600 * 1000,
                "closing_tag": "TIMEOUT",
            },
            {
                "episode_id": "ep_002",
                "started_at_ms": now_ms - closed_ago_ms - 5_000,
                "closed_at_ms": now_ms - closed_ago_ms,
                "closing_tag": "RECHAZO",
            },
        ],
    }


def _farewell_history() -> _History:
    return _History(
        [
            {"role": "user", "content": "Quería moldes y ya conseguí, pero gracias"},
            {"role": "assistant", "content": _FAREWELL},
        ]
    )


def _use_case(store: _Store, loader: _Loader, history: _History):
    return IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )


def _reaction(emoji: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.REACT",
        from_number=_SESSION.removeprefix("wa_"),
        phone_number_id="PID",
        text=None,
        media={"type": "reaction", "message_id": "wamid.FAREWELL", "emoji": emoji},
        timestamp="1714312345",
        msg_type="reaction",
    )


@pytest.mark.asyncio
async def test_emoji_right_after_the_farewell_stays_in_the_chat_without_a_reply() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_farewell(now))
    loader = _Loader()
    history = _farewell_history()

    await _use_case(store, loader, history).execute(_message("☺️👍"))

    assert loader.calls == []
    saved = store.data[_SESSION]
    assert [ep["episode_id"] for ep in saved["episodes"]] == ["ep_001", "ep_002"]
    assert saved["tag"] == "RECHAZO"
    assert saved["last_inbound_at_ms"] >= now
    assert history.events[-1] == {"role": "user", "content": "☺️👍"}


@pytest.mark.asyncio
async def test_a_reaction_to_the_farewell_gets_no_reply_either() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_farewell(now))
    loader = _Loader()
    history = _farewell_history()

    await _use_case(store, loader, history).execute(_reaction("👍"))

    assert loader.calls == []
    assert len(store.data[_SESSION]["episodes"]) == 2
    assert history.events[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_thanks_the_next_day_still_needs_no_new_conversation() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_farewell(now, closed_ago_ms=20 * 3600 * 1000))
    loader = _Loader()

    await _use_case(store, loader, _farewell_history()).execute(
        _message("Muchas gracias 🙏")
    )

    assert loader.calls == []
    assert len(store.data[_SESSION]["episodes"]) == 2


@pytest.mark.asyncio
async def test_a_real_question_after_the_farewell_opens_a_new_conversation() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_farewell(now))
    loader = _Loader()

    await _use_case(store, loader, _farewell_history()).execute(
        _message("Gracias. Oye, ¿y tienen velas de coco?")
    )

    assert len(loader.calls) == 1
    episodes = store.data[_SESSION]["episodes"]
    assert episodes[-1]["episode_id"] == "ep_003"
    assert episodes[-1]["closed_at_ms"] is None


@pytest.mark.asyncio
async def test_an_ack_inside_an_open_episode_still_reaches_the_agent() -> None:
    now = int(time.time() * 1000)
    data = _after_farewell(now)
    data["episodes"][-1].update(closed_at_ms=None, closing_tag=None)
    data["tag"] = "INTERESADO"
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader, _farewell_history()).execute(_message("ok"))

    assert len(loader.calls) == 1


@pytest.mark.asyncio
async def test_an_ack_to_a_later_template_still_reaches_the_agent() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_farewell(now, closed_ago_ms=3 * 24 * 3600 * 1000))
    loader = _Loader()
    history = _farewell_history()
    history.events.append(
        {
            "role": "assistant",
            "kind": "template",
            "content": "Tu pedido está listo. ¿Te lo enviamos hoy?",
        }
    )

    await _use_case(store, loader, history).execute(_message("ok"))

    assert len(loader.calls) == 1


@pytest.mark.asyncio
async def test_an_ack_to_a_campaign_still_reaches_the_agent() -> None:
    now = int(time.time() * 1000)
    data = _after_farewell(now, closed_ago_ms=2 * 24 * 3600 * 1000)
    data["campaign_touches"] = [_touch(now - 60_000)]
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader, _farewell_history()).execute(_message("👍"))

    assert len(loader.calls) == 1
    assert loader.calls[0]["prefer_sales"] is True
