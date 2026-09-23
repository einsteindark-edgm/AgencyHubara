"""Memoria por episodio en el ingest (run 28a8e407, fase 3).

15:33 del 2026-09-23: el pedido #44 cerró el episodio (pago pendiente) y el
cliente escribió "AMOR26". Se abrió un episodio nuevo con la nota "empieza
una conversación nueva, no retomes el pedido", pero el historial del LLM era
el de TODA la sesión: vio el pedido con AMOR26 y contestó sobre él ("el cupón
ya quedó aplicado en tu pedido"). El #330 cortaba el historial solo cuando el
episodio lo abría una campaña.

Contrato: TODO episodio nuevo que sigue a otro (cierre con desenlace, 14 días
sin actividad o campaña) pide cortar el historial del LLM de cada agente, y
su primer mensaje lleva UNA línea con lo que pasó antes. Una pausa dentro
del mismo episodio (INTERESADO no cierra) no corta nada.

Además, si el cliente responde justo a una plantilla (el LLM nunca la ve:
se envía por fuera de su historial), el turno la cita.
"""
from __future__ import annotations

import time

import pytest

from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from tests.plugins.chats.test_campaign_reply import (
    _DAY_MS,
    _SESSION,
    _Loader,
    _message,
    _Store,
    _touch,
)


class _History:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = list(events or [])

    def read_events(self, session_id):
        return list(self.events)

    def append_user_event(self, session_id, content, **_kw) -> None:
        self.events.append({"role": "user", "content": content})


def _use_case(store: _Store, loader: _Loader, history: _History | None = None):
    return IngestInboundMessage(
        history_store=history or _History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )


def _after_order(now_ms: int) -> dict:
    """El episodio del pedido #44: cerrado con pago pendiente, ruta de vuelta
    en ventas."""
    started = now_ms - 40 * 60_000
    return {
        "active_route": "ventas",
        "tag": "CONFIRMADO_PAGO_PENDIENTE",
        "last_inbound_at_ms": now_ms - 25 * 60_000,
        "episodes": [
            {
                "episode_id": "ep_006",
                "started_at_ms": started,
                "closed_at_ms": now_ms - 25 * 60_000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": "order_01TEST",
                "order_draft": {
                    "items": [
                        {"producto": "Cubo Love", "cantidad": "2", "aroma": "Coco cremoso", "color": "Azul"}
                    ]
                },
                "applied_coupon": {"code": "AMOR26"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_new_episode_after_an_order_cuts_the_history_and_says_what_happened() -> None:
    now = int(time.time() * 1000)
    store = _Store(_after_order(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("AMOR26"))

    episodes = store.data[_SESSION]["episodes"]
    assert len(episodes) == 2 and episodes[-1]["closed_at_ms"] is None
    assert episodes[-1]["llm_history_reset"] == {"applied": []}
    first, rest = loader.calls[0]["message"].split("\n", 1)
    assert first.startswith("[Conversación anterior con este cliente")
    assert "pedido registrado (order_01TEST)" in first
    assert "2× Cubo Love (Coco cremoso, Azul)" in first
    assert rest == "AMOR26"


@pytest.mark.asyncio
async def test_episode_abandoned_for_weeks_also_starts_clean() -> None:
    now = int(time.time() * 1000)
    started = now - 20 * _DAY_MS
    store = _Store(
        {
            "active_route": "ventas",
            "tag": "INTERESADO",
            "last_inbound_at_ms": started + 60_000,
            "episodes": [
                {
                    "episode_id": "ep_002",
                    "started_at_ms": started,
                    "closed_at_ms": None,
                    "order_draft": {"slots": {"producto": "Trilogía del Terror"}},
                }
            ],
        }
    )
    loader = _Loader()

    await _use_case(store, loader).execute(_message("Hola"))

    episodes = store.data[_SESSION]["episodes"]
    assert episodes[0]["closing_tag"] == "TIMEOUT"
    assert episodes[-1]["llm_history_reset"] == {"applied": []}
    first = loader.calls[0]["message"].split("\n", 1)[0]
    assert "sin respuesta" in first and "Trilogía del Terror" in first


@pytest.mark.asyncio
async def test_a_pause_inside_the_same_episode_keeps_the_history() -> None:
    now = int(time.time() * 1000)
    data = _after_order(now)
    data["episodes"][0].update(closed_at_ms=None, closing_tag=None, order_id=None)
    data["tag"] = "INTERESADO"
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader).execute(_message("ya volví, ¿en qué íbamos?"))

    episodes = store.data[_SESSION]["episodes"]
    assert len(episodes) == 1
    assert "llm_history_reset" not in episodes[0]
    assert loader.calls[0]["message"] == "ya volví, ¿en qué íbamos?"


@pytest.mark.asyncio
async def test_the_very_first_message_has_nothing_before_it() -> None:
    store = _Store({})
    loader = _Loader()

    await _use_case(store, loader).execute(_message("Hola"))

    assert "llm_history_reset" not in store.data[_SESSION]["episodes"][-1]
    assert loader.calls[0]["message"] == "Hola"


@pytest.mark.asyncio
async def test_reply_to_a_template_quotes_it() -> None:
    now = int(time.time() * 1000)
    data = _after_order(now)
    data["episodes"][0].update(closed_at_ms=None, closing_tag=None, order_id=None)
    store = _Store(data)
    loader = _Loader()
    template = "Hola 🌿 Te quedó sonando el Cubo Love. ¿Lo retomamos?"
    history = _History(
        [
            {"role": "user", "content": "quiero el cubo love"},
            {"role": "assistant", "kind": "template", "content": template},
        ]
    )

    await _use_case(store, loader, history).execute(_message("Sí, cuéntame"))

    assert loader.calls[0]["message"] == (
        f"[El cliente responde a este mensaje que le enviamos: «{template}»]\n"
        "Sí, cuéntame"
    )


@pytest.mark.asyncio
async def test_campaign_reply_is_quoted_once_as_campaign_not_as_template() -> None:
    now = int(time.time() * 1000)
    data = _after_order(now)
    data["campaign_touches"] = [_touch(now - 60_000)]
    store = _Store(data)
    loader = _Loader()
    history = _History(
        [{"role": "assistant", "kind": "template", "content": "Celebra el amor con velas."}]
    )

    await _use_case(store, loader, history).execute(_message("Me gusta"))

    message = loader.calls[0]["message"]
    assert message.count("[El cliente responde a") == 1
    assert "[El cliente responde a la campaña" in message
