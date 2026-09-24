"""Hilo de un turno para el dashboard (plan del laboratorio PR 17).

`trace_view`: la traza v2 con sus pasos en orden (o la v1 sintetizada sin
tiempos), con el paso de la ráfaga primero. La usan el Laboratorio y Chats.

`annotate_turn_keys`: a cada burbuja del chat, el turno que la produjo (el
del cliente, el que la procesó): así Chats pone un botón por turno que abre
su hilo. Un mensaje del operador humano no es de ningún turno del bot.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.plugins.chats.shared.turn_view import annotate_turn_keys, trace_view, turn_key_of

SID = "wa_573001234567"
T0 = 1_790_200_000_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _trace(turn: int, start: int, *, wamids: list[str], sent: list[str], inbound_text: str = "", key: str | None = None) -> dict:
    return {
        "turn": turn, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": start,
        "turn_key": key, "inbound_text": inbound_text,
        "inbound": [{"seq": i + 1, "wamid": w, "text": inbound_text} for i, w in enumerate(wamids)],
        "sent_texts": sent,
    }


def test_a_v2_trace_starts_with_the_burst_step() -> None:
    trace = {**_trace(1, T0, wamids=["wamid.A"], sent=["¡Hola!"], inbound_text="hola"),
             "steps": [{"i": 1, "at_ms": 5, "kind": "llm"}]}

    view = trace_view(trace)

    assert view["fidelity"] == "v2"
    assert [s["kind"] for s in view["steps"]] == ["inbound", "llm"]
    assert view["steps"][0]["messages"][0]["wamid"] == "wamid.A"


def test_a_v1_trace_is_synthesized_without_times() -> None:
    view = trace_view({"inbound_text": "hola\nprecio?", "tools": [{"name": "search_products", "ok": True}],
                       "guards": ["variant_picker"], "sent_texts": ["Claro"]})

    assert view["fidelity"] == "v1"
    assert [s["kind"] for s in view["steps"]] == ["inbound", "tool", "guard", "outbound"]
    assert all(s["at_ms"] is None for s in view["steps"])


def test_the_turn_key_is_the_trace_one_or_a_synthesized_one() -> None:
    assert turn_key_of({"turn_key": "run:abc/t:3"}, SID) == "run:abc/t:3"
    assert turn_key_of({"turn": 2, "episode_id": "ep_001"}, SID) == f"{SID}/ep_001/t2"


def test_each_bubble_gets_the_turn_that_produced_it() -> None:
    t1 = _trace(1, T0 + 2_000, wamids=["wamid.A"], sent=["¡Hola! Bienvenida"], inbound_text="hola", key="run:x/t:1")
    t2 = _trace(2, T0 + 70_000, wamids=["wamid.B", "wamid.C"], sent=["Te dejo el catálogo"], inbound_text="catálogo\nenvío",
                key="run:x/t:2")
    messages = [
        {"ui_type": "user_message", "content": "hola", "timestamp": _iso(T0), "wamid": "wamid.A"},
        {"ui_type": "agent_message", "content": "¡Hola! Bienvenida", "timestamp": _iso(T0 + 6_000)},
        {"ui_type": "user_message", "content": "catálogo", "timestamp": _iso(T0 + 60_000), "wamid": "wamid.B"},
        {"ui_type": "user_message", "content": "envío", "timestamp": _iso(T0 + 66_000)},
        {"ui_type": "agent_tool_call", "content": "", "timestamp": _iso(T0 + 73_000)},
        {"ui_type": "agent_message", "content": "Te dejo el catálogo", "timestamp": _iso(T0 + 75_000)},
        {"ui_type": "human_message", "content": "te escribe una persona", "timestamp": _iso(T0 + 80_000)},
    ]

    annotate_turn_keys(messages, [t2, t1], SID)

    assert [m.get("turn_key") for m in messages] == [
        "run:x/t:1", "run:x/t:1", "run:x/t:2", "run:x/t:2", "run:x/t:2", "run:x/t:2", None,
    ]


def test_without_traces_nothing_is_annotated() -> None:
    messages = [{"ui_type": "user_message", "content": "hola", "timestamp": _iso(T0)}]

    annotate_turn_keys(messages, [], SID)

    assert "turn_key" not in messages[0]


def test_a_customer_message_long_before_any_turn_is_not_guessed() -> None:
    t1 = _trace(1, T0 + 3 * 3_600_000, wamids=["wamid.Z"], sent=["ok"], key="run:x/t:1")
    messages = [{"ui_type": "user_message", "content": "hola", "timestamp": _iso(T0)}]

    annotate_turn_keys(messages, [t1], SID)

    assert messages[0].get("turn_key") is None


def test_a_template_sent_long_after_the_turn_is_not_that_turn() -> None:
    """ETA, remarketing o una campaña mandan su plantilla minutos u horas
    después: no la produjo el turno anterior del bot (su hilo no la trae)."""
    t1 = {**_trace(1, T0, wamids=["wamid.A"], sent=["¡Hola!"], key="run:x/t:1"), "recorded_at_ms": T0 + 9_000}
    messages = [
        {"ui_type": "agent_message", "content": "¡Hola!", "timestamp": _iso(T0 + 7_000)},
        {"ui_type": "agent_message", "kind": "template", "content": "Tu pedido va en camino", "timestamp": _iso(T0 + 15 * 60_000)},
    ]

    annotate_turn_keys(messages, [t1], SID)

    assert [m.get("turn_key") for m in messages] == ["run:x/t:1", None]


def test_an_echo_of_another_agent_is_not_a_turn_of_the_bot() -> None:
    t1 = _trace(1, T0, wamids=["wamid.A"], sent=["¡Hola!"], key="run:x/t:1")
    messages = [{"ui_type": "agent_message", "sender": "mba", "content": "Hola, soy el asistente de Meta", "timestamp": _iso(T0 + 5_000)}]

    annotate_turn_keys(messages, [t1], SID)

    assert messages[0].get("turn_key") is None


def test_a_broken_trace_does_not_break_the_annotation() -> None:
    broken = {"turn": 1, "turn_started_ms": True, "inbound": [{"wamid": ["no", "hashable"]}, "texto suelto"]}
    weird = {**_trace(2, T0, wamids=[], sent=["ok"], key="run:x/t:2"), "inbound": [{"wamid": {"x": 1}}]}
    messages = [
        {"ui_type": "user_message", "content": "hola", "timestamp": _iso(T0 - 1_000), "wamid": "wamid.A"},
        {"ui_type": "agent_message", "content": "ok", "timestamp": _iso(T0 + 4_000)},
    ]

    annotate_turn_keys(messages, [broken, weird], SID)

    assert [m.get("turn_key") for m in messages] == ["run:x/t:2", "run:x/t:2"]
