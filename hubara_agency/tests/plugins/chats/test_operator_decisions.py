"""Las decisiones de la App Operador corren por el motor de decisiones oficial (PR #372).

Qué burbuja va primero en el chat (`burbuja`) y cómo se clasifica cada incendio
de chat (`incendio`) son capacidades del paquete `operador`
(`chats/shared/operator/decisions/`), resueltas como las de ventas
(`BundledCapability`) y decididas por `decide_for_session`: el modo de cada
conversación sale del panel «Motor de decisiones» (`_rollout/decisions.json`,
dentro del techo `SALES_CAPABILITIES_CEILING`), y el motor deja la decisión en
la conversación, sus métricas, la cola de desacuerdos y el costo de Jev. Las
reglas de `mobile_rules` arman lo legal y son el respaldo.

Lo único propio de la app es la latencia: nunca espera a Jev (la burbuja, a lo
sumo `BUBBLE_WAIT_S`; los incendios, nada) y guarda el veredicto para la misma
versión del chat.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.shared.mobile_rules import (
    ChatFireFacts,
    DraftItemFacts,
    OrderFireFacts,
    SuggestionFacts,
    detect_fires,
    suggest_actions,
)
from src.sdk import connectorkit
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


@pytest.fixture
def vault(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.delenv("SALES_PERCEPTION_PROFILE", raising=False)
    return _isolate_vault_dir


def _jev(monkeypatch: pytest.MonkeyPatch, answers: dict[str, TypedAnswer] | None = None, **kw: Any) -> RecordingPort:
    port = RecordingPort(answers, **kw)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)
    return port


def _choice(qid: str, option: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=option, probs=((option, p),), confidence=p)


def _engine(vault: Path) -> Any:
    from src.plugins.chats.api.mobile_decisions import OperatorDecisions

    return OperatorDecisions(vault_dir=vault, now_ms=lambda: NOW)


def _seed(vault: Path, session: str, events: list[dict[str, Any]]) -> None:
    d = vault / session
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps({"episodes": [
        {"episode_id": "ep_1", "started_at_ms": NOW - 60 * _MIN, "closed_at_ms": None}
    ]}), encoding="utf-8")
    (d / "sessions" / f"{session}.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


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


# ── el panel manda ───────────────────────────────────────────────────────────


def test_the_panel_has_a_switch_for_each_app_decision() -> None:
    assert {"burbuja", "incendio"} <= set(bots.CAPABILITIES)


async def test_with_the_decision_off_jev_is_never_asked(vault: Path, monkeypatch) -> None:
    port = _jev(monkeypatch)
    _seed(vault, LAURA, _CHAT)

    got = await _engine(vault).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got == _bubbles() and port.asked == []


async def test_on_jev_picks_which_legal_bubble_goes_first(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    port = _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(vault, LAURA, _CHAT)

    got = await _engine(vault).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got["decided_by"] == "jev"
    assert [(s["label"], s["prominence"]) for s in got["suggestions"]] == [
        ("Más fotos", "primary"), ("Enviar aromas", "normal"),
    ]
    state, questions = port.asked[0]
    assert "más fotos?" in state and "Dúo Zodiacal" in state
    assert set(questions[0].options) == {"enviar_aromas", "mas_fotos", "ninguna"}


async def test_a_doubtful_jev_leaves_the_order_of_the_rules(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.4)})
    _seed(vault, LAURA, _CHAT)

    assert await _engine(vault).suggestions(_bubbles(), events=_CHAT, metadata={}) == _bubbles()


async def test_none_of_them_means_no_bubble_is_highlighted(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "ninguna", 0.9)})
    _seed(vault, LAURA, _CHAT)

    got = await _engine(vault).suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got["decided_by"] == "jev"
    assert [(s["label"], s["prominence"]) for s in got["suggestions"]] == [
        ("Enviar aromas", "normal"), ("Más fotos", "normal"),
    ]


async def test_jev_down_means_the_rules(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    _jev(monkeypatch, error="timeout")
    _seed(vault, LAURA, _CHAT)

    assert await _engine(vault).suggestions(_bubbles(), events=_CHAT, metadata={}) == _bubbles()


async def test_no_bubbles_nothing_to_ask(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    port = _jev(monkeypatch)
    payload = {**_bubbles(), "suggestions": []}

    assert await _engine(vault).suggestions(payload, events=_CHAT, metadata={}) == payload
    assert port.asked == []


async def test_in_shadow_the_engine_records_everything_and_the_app_sees_the_rules(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "shadow"})
    _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)}, cost_usd=0.002)
    _seed(vault, LAURA, _CHAT)
    engine = _engine(vault)

    got = await engine.suggestions(_bubbles(), events=_CHAT, metadata={})
    await engine.drain()

    assert got == _bubbles()
    # La conversación (Calidad LLM), la cola de desacuerdos, las métricas del panel y el costo: el motor oficial.
    [row] = _rows(vault / LAURA / "evals" / "decisions.jsonl")
    assert (row["stage"], row["capability"], row["provider"], row["jev"], row["bundle"]) == (
        "operador", "burbuja", "sombra", "1", "operador@1"
    )
    assert [d["capability"] for d in _rows(vault / "_decisions" / "disagreements.jsonl")] == ["burbuja"]
    metrics = [r for path in (vault / "_decisions" / "metrics").glob("*.jsonl") for r in _rows(path)]
    assert [(m["capability"], m["provider"]) for m in metrics] == [("burbuja", "sombra")]
    metadata = json.loads((vault / LAURA / "metadata.json").read_text())
    assert metadata["episodes"][-1]["jev_usage"]["calls"] == 1


async def test_the_same_chat_version_asks_jev_once(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"burbuja": "on"})
    port = _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(vault, LAURA, _CHAT)
    engine = _engine(vault)

    first = await engine.suggestions(_bubbles(), events=_CHAT, metadata={})
    second = await engine.suggestions(_bubbles(), events=_CHAT, metadata={})

    assert first == second and len(port.asked) == 1


async def test_turning_the_decision_on_takes_effect_without_waiting_for_a_new_message(vault: Path, monkeypatch) -> None:
    _jev(monkeypatch, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)})
    _seed(vault, LAURA, _CHAT)
    engine = _engine(vault)
    assert await engine.suggestions(_bubbles(), events=_CHAT, metadata={}) == _bubbles()

    bots.write_capability_modes(vault, {"burbuja": "on"})
    got = await engine.suggestions(_bubbles(), events=_CHAT, metadata={})

    assert got["decided_by"] == "jev" and got["suggestions"][0]["label"] == "Más fotos"


# ── incendios ────────────────────────────────────────────────────────────────


_ANGRY = [
    {"role": "assistant", "content": "Te escribe Liliana 🤍", "sender": "human", "timestamp": "2027-01-15T08:00:00+00:00"},
    {"role": "user", "content": "llevo dos días esperando el pedido, esto es una falta de respeto",
     "timestamp": "2027-01-15T08:03:00+00:00"},
]


def _chat_fire(reason: str | None = None, *, unanswered: int = 1, last_ms: int = NOW - 3 * _MIN) -> ChatFireFacts:
    return ChatFireFacts(session_id=SOFIA, name="Sofía Prueba", in_human=True, escalation_reason=reason,
                         unanswered_count=unanswered, waiting_since_ms=last_ms, last_inbound_ms=last_ms)


async def _fires(engine: Any, chats: list[ChatFireFacts], events: list[dict[str, Any]]) -> tuple[list[dict], bool]:
    cards = detect_fires(chats, [], now_ms=NOW, today_iso="2027-01-15")
    return await engine.fires(cards, chats={c.session_id: c for c in chats}, events_for=lambda _sid: events)


async def test_fires_never_wait_for_jev_and_use_its_reading_on_the_next_look(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"incendio": "on"})
    port = _jev(monkeypatch, {
        "incendio.gravedad": _choice("incendio.gravedad", "grave", 0.9),
        "incendio.tipo": _choice("incendio.tipo", "queja", 0.9),
    })
    _seed(vault, SOFIA, _ANGRY)
    engine = _engine(vault)

    first, used_first = await _fires(engine, [_chat_fire()], _ANGRY)
    await engine.drain()
    second, used_second = await _fires(engine, [_chat_fire()], _ANGRY)

    # Por reglas lleva 3 min sin respuesta: «hoy». Jev lee la queja: grave.
    assert (first[0]["severity"], first[0]["kind"], used_first) == ("hoy", "other", False)
    assert (second[0]["severity"], second[0]["kind"], used_second) == ("grave", "angry", True)
    assert len(port.asked) == 1 and "falta de respeto" in port.asked[0][0]


async def test_a_health_topic_stays_grave_whatever_jev_says(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"incendio": "on"})
    _jev(monkeypatch, {
        "incendio.gravedad": _choice("incendio.gravedad", "puede_esperar", 0.95),
        "incendio.tipo": _choice("incendio.tipo", "salud", 0.9),
    })
    _seed(vault, SOFIA, _ANGRY)
    engine = _engine(vault)

    await _fires(engine, [_chat_fire(reason="HEALTH_SAFETY")], _ANGRY)
    await engine.drain()
    cards, _used = await _fires(engine, [_chat_fire(reason="HEALTH_SAFETY")], _ANGRY)

    assert (cards[0]["severity"], cards[0]["kind"]) == ("grave", "health")


async def test_jev_compares_with_its_previous_reading_to_say_it_gets_worse(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"incendio": "on"})
    port = _jev(monkeypatch, {
        "incendio.gravedad": _choice("incendio.gravedad", "hoy", 0.9),
        "incendio.tipo": _choice("incendio.tipo", "estado_pedido", 0.9),
        "incendio.empeora": TypedAnswer(id="incendio.empeora", kind="noul", p=0.9),
    })
    _seed(vault, SOFIA, _ANGRY)
    engine = _engine(vault)
    await _fires(engine, [_chat_fire()], _ANGRY)
    await engine.drain()

    more = [*_ANGRY, {"role": "user", "content": "??", "timestamp": "2027-01-15T08:05:00+00:00"}]
    await _fires(engine, [_chat_fire(unanswered=2, last_ms=NOW - _MIN)], more)
    await engine.drain()
    cards, _used = await _fires(engine, [_chat_fire(unanswered=2, last_ms=NOW - _MIN)], more)

    assert "incendio.empeora" not in [q.id for q in port.asked[0][1]]
    second_state, second_questions = port.asked[1]
    assert "incendio.empeora" in [q.id for q in second_questions] and "Evaluación anterior" in second_state
    assert (cards[0]["kind"], cards[0]["getting_worse"]) == ("asking_status", True)


async def test_order_fires_are_facts_and_jev_is_not_asked(vault: Path, monkeypatch) -> None:
    bots.write_capability_modes(vault, {"incendio": "on"})
    port = _jev(monkeypatch)
    engine = _engine(vault)
    order = OrderFireFacts(session_id=SOFIA, order_id="order_1", display_id="41", name="Sofía", total_cop=89900,
                           stage="preparing", due_iso="2027-01-10", overdue_since_ms=None, payment_pending=False,
                           pending_since_ms=None)
    cards = detect_fires([], [order], now_ms=NOW, today_iso="2027-01-15")

    got, used = await engine.fires(cards, chats={}, events_for=lambda _sid: [])
    await engine.drain()

    assert got == cards and used is False and port.asked == []


@pytest.fixture(autouse=True)
def _fresh_bundle():
    yield
    import importlib
    import importlib.util

    if importlib.util.find_spec("src.plugins.chats.shared.operator.decisions") is not None:
        importlib.import_module("src.plugins.chats.shared.operator.decisions").reset()
