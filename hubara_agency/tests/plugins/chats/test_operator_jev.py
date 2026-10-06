"""Jev en la App Operador: qué burbuja va primero y cómo se clasifica cada incendio.

Las reglas (`mobile_rules`) arman lo legal (las burbujas que se pueden enviar,
los chats que esperan a un humano) y deciden si Jev duda o no responde. Jev,
con el paquete `operador`, elige la burbuja principal y la gravedad, el tipo y
si empeora de cada incendio de chat. El interruptor es `OPERATOR_APP_JEV`
(off | shadow | on, Terraform `lab.operator_app_jev`): en sombra Jev contesta y
queda registrado, pero la app sigue viendo las reglas. La app nunca espera a
Jev más de un par de segundos: lo que no llegó a tiempo sale por reglas y la
respuesta queda guardada para la misma versión del chat.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from src.plugins.chats.shared.mobile_rules import (
    ChatFireFacts,
    DraftItemFacts,
    SuggestionFacts,
    detect_fires,
    suggest_actions,
)
from src.sdk.connectorkit import FakePerceptionAdapter, PerceptionResult, TypedAnswer, TypedQuestion

NOW = 1_800_000_000_000
_MIN = 60_000
LAURA = "wa_000000000101"
SOFIA = "wa_000000000102"


class RecordingPort:
    """El Jev falso, guardando las preguntas completas (texto y opciones)."""

    name = "fake"
    model = "fake"

    def __init__(self, answers: dict[str, TypedAnswer] | None = None, *, error: str | None = None,
                 cost_usd: float = 0.0) -> None:
        self._fake = FakePerceptionAdapter(answers, error=error, cost_usd=cost_usd)
        self.asked: list[tuple[str, list[TypedQuestion]]] = []

    async def ask(self, state: str, questions: Sequence[TypedQuestion], *, timeout_s: float,
                  redact: Sequence[str] = ()) -> PerceptionResult:
        self.asked.append((state, list(questions)))
        return await self._fake.ask(state, questions, timeout_s=timeout_s, redact=redact)


def _choice(qid: str, option: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=option, probs=((option, p),), confidence=p)


def _jev(port: Any, vault: Path, mode: str = "on") -> Any:
    from src.plugins.chats.api.mobile_jev import OperatorJev

    return OperatorJev(port=port, vault_dir=vault, now_ms=lambda: NOW, mode=lambda: mode)


def _seed(vault: Path, session: str, events: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> None:
    d = vault / session
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(metadata or {"episodes": [
        {"episode_id": "ep_1", "started_at_ms": NOW - 60 * _MIN, "closed_at_ms": None}
    ]}), encoding="utf-8")
    (d / "sessions" / f"{session}.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )


_CHAT = [
    {"role": "user", "content": "Hola, ¿tienen el Dúo Zodiacal?", "timestamp": "2027-01-15T08:00:00+00:00"},
    {"role": "assistant", "content": "¡Hola! Sí, a $89.900. ¿Para quién sería?", "timestamp": "2027-01-15T08:01:00+00:00"},
    {"role": "user", "content": "quiero 2, ¿me muestras más fotos?", "timestamp": "2027-01-15T08:02:00+00:00"},
]


def _bubbles() -> dict[str, Any]:
    """Las reglas en `etapa_variantes`: «Enviar aromas» primero y «Más fotos» después."""
    return suggest_actions(SuggestionFacts(
        session_id=LAURA, version=7, stage="etapa_variantes", window_open=True, in_control="human",
        catalog_available=True,
        items=(DraftItemFacts(handle="duo-zodiacal", quantity=2, missing=("aroma",), image_count=3),),
    ))


# ── burbujas ─────────────────────────────────────────────────────────────────


async def test_off_never_asks_jev_and_leaves_the_rules(tmp_path: Path) -> None:
    port = RecordingPort()
    _seed(tmp_path, LAURA, _CHAT)

    got = await _jev(port, tmp_path, mode="off").suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got == _bubbles() and port.asked == []


async def test_jev_picks_which_legal_bubble_goes_first(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(tmp_path, LAURA, _CHAT)

    got = await _jev(port, tmp_path).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got["decided_by"] == "jev"
    assert [(s["label"], s["prominence"]) for s in got["suggestions"]] == [
        ("Más fotos", "primary"), ("Enviar aromas", "normal"),
    ]
    state, questions = port.asked[0]
    # Jev ve la conversación y elige SOLO entre las jugadas legales (más «ninguna»).
    assert "más fotos?" in state and "Dúo Zodiacal" in state
    assert set(questions[0].options) == {"enviar_aromas", "mas_fotos", "ninguna"}


async def test_a_doubtful_jev_leaves_the_order_of_the_rules(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.4)})
    _seed(tmp_path, LAURA, _CHAT)

    got = await _jev(port, tmp_path).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got == _bubbles()


async def test_none_of_them_means_no_bubble_is_highlighted(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "ninguna", 0.9)})
    _seed(tmp_path, LAURA, _CHAT)

    got = await _jev(port, tmp_path).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got["decided_by"] == "jev"
    assert [s["prominence"] for s in got["suggestions"]] == ["normal", "normal"]
    assert [s["label"] for s in got["suggestions"]] == ["Enviar aromas", "Más fotos"]


async def test_jev_down_means_the_rules(tmp_path: Path) -> None:
    port = RecordingPort(error="timeout")
    _seed(tmp_path, LAURA, _CHAT)

    got = await _jev(port, tmp_path).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got == _bubbles()


async def test_no_bubbles_nothing_to_ask(tmp_path: Path) -> None:
    port = RecordingPort()
    payload = {**_bubbles(), "suggestions": []}

    got = await _jev(port, tmp_path).suggestions(payload, events=_CHAT, metadata={})

    assert got == payload and port.asked == []


async def test_shadow_asks_and_records_but_the_app_sees_the_rules(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(tmp_path, LAURA, _CHAT)
    jev = _jev(port, tmp_path, mode="shadow")

    got = await jev.suggestions(_bubbles(), events=_CHAT, metadata={})
    await jev.drain()

    assert got == _bubbles()
    rows = [json.loads(line) for line in (tmp_path / LAURA / "evals" / "decisions.jsonl").read_text().splitlines()]
    assert [(r["stage"], r["capability"], r["by"], r["bundle"]) for r in rows] == [
        ("operador", "burbuja", "jev", "operador@1")
    ]


async def test_the_same_chat_version_asks_jev_once(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(tmp_path, LAURA, _CHAT)
    jev = _jev(port, tmp_path)

    first = await jev.suggestions(_bubbles(), events=_CHAT, metadata={})
    second = await jev.suggestions(_bubbles(), events=_CHAT, metadata={})

    assert first == second and len(port.asked) == 1


async def test_what_jev_costs_is_added_to_the_conversation(tmp_path: Path) -> None:
    port = RecordingPort({"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)}, cost_usd=0.002)
    _seed(tmp_path, LAURA, _CHAT)

    await _jev(port, tmp_path).suggestions(_bubbles(), events=_CHAT, metadata={})

    metadata = json.loads((tmp_path / LAURA / "metadata.json").read_text())
    assert metadata["episodes"][-1]["jev_usage"]["calls"] == 1


# ── incendios ────────────────────────────────────────────────────────────────


_ANGRY = [
    {"role": "assistant", "content": "Te escribe Liliana 🤍", "sender": "human", "timestamp": "2027-01-15T08:00:00+00:00"},
    {"role": "user", "content": "llevo dos días esperando el pedido, esto es una falta de respeto",
     "timestamp": "2027-01-15T08:03:00+00:00"},
]


def _chat_fire(session: str = SOFIA, reason: str | None = None, *, unanswered: int = 1,
               last_ms: int = NOW - 3 * _MIN) -> ChatFireFacts:
    return ChatFireFacts(session_id=session, name="Sofía Prueba", in_human=True, escalation_reason=reason,
                         unanswered_count=unanswered, waiting_since_ms=last_ms, last_inbound_ms=last_ms)


async def _fires(jev: Any, chats: list[ChatFireFacts], events: list[dict[str, Any]]) -> tuple[list[dict], bool]:
    cards = detect_fires(chats, [], now_ms=NOW, today_iso="2027-01-15")
    return await jev.fires(cards, chats={c.session_id: c for c in chats}, events_for=lambda _sid: events)


async def test_fires_never_wait_for_jev_and_use_its_reading_on_the_next_look(tmp_path: Path) -> None:
    port = RecordingPort({
        "incendio.gravedad": _choice("incendio.gravedad", "grave", 0.9),
        "incendio.tipo": _choice("incendio.tipo", "queja", 0.9),
    })
    _seed(tmp_path, SOFIA, _ANGRY)
    jev = _jev(port, tmp_path)

    first, used_first = await _fires(jev, [_chat_fire()], _ANGRY)
    await jev.drain()
    second, used_second = await _fires(jev, [_chat_fire()], _ANGRY)

    # Por reglas lleva 3 min sin respuesta: «hoy». Jev lee la queja: grave.
    assert (first[0]["severity"], first[0]["kind"], used_first) == ("hoy", "other", False)
    assert (second[0]["severity"], second[0]["kind"], used_second) == ("grave", "angry", True)
    assert len(port.asked) == 1 and "falta de respeto" in port.asked[0][0]


async def test_a_health_topic_stays_grave_whatever_jev_says(tmp_path: Path) -> None:
    port = RecordingPort({
        "incendio.gravedad": _choice("incendio.gravedad", "puede_esperar", 0.95),
        "incendio.tipo": _choice("incendio.tipo", "salud", 0.9),
    })
    _seed(tmp_path, SOFIA, _ANGRY)
    jev = _jev(port, tmp_path)

    await _fires(jev, [_chat_fire(reason="HEALTH_SAFETY")], _ANGRY)
    await jev.drain()
    cards, _used = await _fires(jev, [_chat_fire(reason="HEALTH_SAFETY")], _ANGRY)

    assert (cards[0]["severity"], cards[0]["kind"]) == ("grave", "health")


async def test_jev_compares_with_its_previous_reading_to_say_it_gets_worse(tmp_path: Path) -> None:
    port = RecordingPort({
        "incendio.gravedad": _choice("incendio.gravedad", "hoy", 0.9),
        "incendio.tipo": _choice("incendio.tipo", "estado_pedido", 0.9),
        "incendio.empeora": TypedAnswer(id="incendio.empeora", kind="noul", p=0.9),
    })
    _seed(tmp_path, SOFIA, _ANGRY)
    jev = _jev(port, tmp_path)
    await _fires(jev, [_chat_fire()], _ANGRY)
    await jev.drain()

    more = [*_ANGRY, {"role": "user", "content": "??", "timestamp": "2027-01-15T08:05:00+00:00"}]
    await _fires(jev, [_chat_fire(unanswered=2, last_ms=NOW - _MIN)], more)
    await jev.drain()
    cards, _used = await _fires(jev, [_chat_fire(unanswered=2, last_ms=NOW - _MIN)], more)

    first_ids = [q.id for q in port.asked[0][1]]
    second_state, second_questions = port.asked[1]
    assert "incendio.empeora" not in first_ids
    assert "incendio.empeora" in [q.id for q in second_questions] and "Evaluación anterior" in second_state
    assert (cards[0]["kind"], cards[0]["getting_worse"]) == ("asking_status", True)


async def test_order_fires_are_facts_and_jev_is_not_asked(tmp_path: Path) -> None:
    from src.plugins.chats.shared.mobile_rules import OrderFireFacts

    port = RecordingPort()
    jev = _jev(port, tmp_path)
    order = OrderFireFacts(session_id=SOFIA, order_id="order_1", display_id="41", name="Sofía", total_cop=89900,
                           stage="preparing", due_iso="2027-01-10", overdue_since_ms=None, payment_pending=False,
                           pending_since_ms=None)
    cards = detect_fires([], [order], now_ms=NOW, today_iso="2027-01-15")

    got, used = await jev.fires(cards, chats={}, events_for=lambda _sid: [])
    await jev.drain()

    assert got == cards and used is False and port.asked == []


@pytest.fixture(autouse=True)
def _fresh_bundle():
    yield
    import importlib
    import importlib.util

    if importlib.util.find_spec("src.plugins.chats.shared.operator.decisions") is not None:
        importlib.import_module("src.plugins.chats.shared.operator.decisions").reset()
