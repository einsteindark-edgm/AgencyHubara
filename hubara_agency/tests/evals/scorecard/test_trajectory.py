"""Trayectoria de un episodio (HU-SC-1): la estructura única que evalúan los
checks y que dibuja la tira del dashboard. Lo que se ve es lo que se evaluó."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    build_legacy_trajectory,
    build_trajectory,
)


def _trace(turn: int, **over) -> dict:
    base = {
        "v": 1,
        "session_id": "wa_100000000001",
        "episode_id": "ep_007",
        "turn": turn,
        "recorded_at_ms": 1_000 * turn,
        "trigger": "customer",
        "inbound_text": "hola",
        "turn_started_ms": 1_000 * turn - 500,
        "first_contact": turn == 1,
        "tools": [],
        "discarded_narration": [],
        "llm_text": "",
        "sent_texts": [],
        "suppressed_reason": None,
        "guards": [],
        "stage_in": "descubrimiento",
        "stage_out": "descubrimiento",
        "draft": {},
        "confirmed": False,
        "confirmed_by": None,
        "signal": None,
        "state": {"tag": "NO_ETIQUETADO", "route": "ventas", "escalation_reason": None,
                  "closing_tag": None, "order_id": None, "changes": []},
    }
    base.update(over)
    return base


def test_build_trajectory_orders_turns_and_derives_intents_from_ok_tools() -> None:
    traces = [
        _trace(2, tools=[
            {"name": "search_products", "ok": True, "error": None, "notes": ["count:23"], "args": {"q": "café"}},
            {"name": "present_products", "ok": True, "error": None, "notes": [], "args": {}},
            {"name": "request_shipping_details", "ok": False, "error": "purchase_not_confirmed", "notes": [], "args": {}},
        ], guards=["variant_enumeration_guard"]),
        _trace(1, sent_texts=["¡Buenos días! Bienvenido a *Hubara*"], tools=[
            {"name": "send_quick_replies", "ok": True, "error": None, "notes": [], "args": {}},
        ]),
    ]

    traj = build_trajectory(
        traces, session_id="wa_100000000001", episode={"episode_id": "ep_007", "closing_tag": "INTERESADO"}
    )

    assert traj.fidelity == "trace"
    assert [t.turn for t in traj.turns] == [1, 2]
    assert traj.turns[0].intents == ("quick_replies",)
    # Solo las tools que hicieron su efecto generan componente; el guard suma el picker.
    assert traj.turns[1].intents == ("products_list", "variant_picker")
    assert traj.turns[1].tool("request_shipping_details").error == "purchase_not_confirmed"
    assert traj.closing_tag == "INTERESADO"
    assert traj.first_contact is True


def test_the_form_the_safety_net_sent_counts_as_sent() -> None:
    """Incidente del 2026-10-09: el bot prometió el formulario sin la tool y
    la red lo mandó (`ensure_promised_handoff_activity`). Ninguna tool del LLM
    lo pidió, pero salió en el flush del turno: el cliente lo vio. Uno que no
    llegó no cuenta."""
    sent = _trace(1, steps=[{"kind": "outbound", "at_ms": 9, "bubbles": [
        {"kind": "shipping_flow", "wamid": "wamid.f1", "delivered": True},
    ]}])
    lost = _trace(2, steps=[{"kind": "outbound", "at_ms": 9, "bubbles": [
        {"kind": "shipping_flow", "wamid": None, "delivered": False},
    ]}])

    traj = build_trajectory([sent, lost], session_id="wa_100000000001", episode={"episode_id": "ep_007"})

    assert traj.turns[0].intents == ("shipping_flow",)
    assert traj.turns[1].intents == ()


def test_a_handoff_from_remarketing_is_read_as_a_handoff_not_as_the_customer() -> None:
    # La plataforma ya no marca el traspaso en `trigger` (siempre «customer»):
    # el turno que es SOLO un traspaso trae el encuadre de la plataforma como
    # `inbound_text`. Leído como mensaje del cliente, los checks le exigían el
    # saludo de un primer contacto y leían «catálogo» en las instrucciones.
    from src.platform.workflow_helpers import _handoff_takeover_framing

    framing = _handoff_takeover_framing("Usuario respondió: Hola, sí quiero ver")
    traj = build_trajectory(
        [_trace(1, inbound_text=framing, first_contact=True)],
        session_id="wa_100000000001",
        episode={"episode_id": "ep_007"},
    )

    turn = traj.turns[0]
    assert (turn.trigger, turn.inbound_text, turn.first_contact) == (
        "handoff",
        "Usuario respondió: Hola, sí quiero ver",
        False,
    )


_FORM_MESSAGE = "Para enviarte tu pedido necesito unos datos 🤍\n\n• *2× Velón Koala* (Blanco · Lavanda)"


def test_the_text_the_code_wrote_with_a_card_is_read_by_the_customer() -> None:
    """Incidente 2026-10-06 (turno 9): el cliente leyó el mensaje del
    formulario, que arma el código. La traza lo trae en `card_text` y
    `read_texts` lo cuenta, después de los textos (el flush va al final)."""
    traces = [
        _trace(1, sent_texts=["Listo"], tools=[
            {"name": "request_shipping_details", "ok": True, "error": None, "notes": [], "args": {},
             "card_text": _FORM_MESSAGE},
        ]),
    ]

    turn = build_trajectory(traces, session_id="wa_100000000001", episode={"episode_id": "ep_007"}).turns[0]

    assert turn.tool("request_shipping_details").card_text == _FORM_MESSAGE
    assert turn.read_texts == ("Listo", _FORM_MESSAGE)


def test_the_text_of_a_rejected_card_is_not_read() -> None:
    traces = [
        _trace(1, tools=[
            {"name": "request_shipping_details", "ok": False, "error": "purchase_not_confirmed", "notes": [],
             "args": {}, "card_text": _FORM_MESSAGE},
        ]),
    ]

    turn = build_trajectory(traces, session_id="wa_100000000001", episode={"episode_id": "ep_007"}).turns[0]

    assert turn.read_texts == ()


def test_build_trajectory_without_traces_is_empty() -> None:
    traj = build_trajectory([], session_id="wa_100000000001", episode={"episode_id": "ep_001"})

    assert traj.fidelity == "empty"
    assert traj.turns == ()


def test_trajectory_to_dict_is_json_ready() -> None:
    import json

    traj = build_trajectory([_trace(1)], session_id="wa_100000000001", episode={"episode_id": "ep_007"})

    data = traj.to_dict()
    assert json.loads(json.dumps(data))["turns"][0]["turn"] == 1
    assert data["fidelity"] == "trace"


def test_build_legacy_trajectory_groups_dashboard_events_into_turns() -> None:
    events = [
        {"role": "user", "content": "Hola", "timestamp": "2026-09-14T14:53:00+00:00"},
        {"role": "assistant", "content": "¡Buenos días! Bienvenido a *Hubara*", "timestamp": "2026-09-14T14:53:05+00:00"},
        {"role": "assistant", "kind": "ui_component", "component_kind": "quick_replies", "content": "botones"},
        {"role": "user", "content": "Voy apenas en camino a casa"},
        {"role": "assistant", "kind": "ui_component", "component_kind": "shipping_flow", "content": "form"},
        {"role": "assistant", "content": "¿Lo confirmamos?", "tools_used": ["request_shipping_details"]},
        {"role": "assistant", "sender": "human", "content": "Hola, soy Liliana"},
        {"role": "user", "content": "gracias"},
    ]

    traj = build_legacy_trajectory(
        events, session_id="wa_100000000001", episode={"episode_id": "ep_007", "closing_tag": "CONFIRMADO_SIN_DATOS"}
    )

    assert traj.fidelity == "legacy"
    assert [t.turn for t in traj.turns] == [1, 2]  # corta en el takeover humano
    assert traj.turns[0].sent_texts == ("¡Buenos días! Bienvenido a *Hubara*",)
    assert traj.turns[0].intents == ("quick_replies",)
    assert traj.turns[1].intents == ("shipping_flow",)
    assert traj.turns[1].signal == "deferral"  # re-detectada con purchase_signals
    assert traj.turns[1].tool("request_shipping_details").ok is None  # desconocido
    assert traj.turns[1].stage_out is None


def test_legacy_trajectory_drops_proactive_messages_from_other_agents() -> None:
    """El JSONL del dashboard mezcla al asesor de ventas con remarketing y con
    las notificaciones de envío (sin campo que las distinga). Un mensaje del bot
    que llega mucho después del último mensaje del cliente no es respuesta del
    asesor: se excluye de la trayectoria (primer informe, 9-15 sep: el 🚚 de la
    notificación de envío reprobó EST-02 al asesor)."""
    events = [
        {"role": "user", "content": "¿Qué formas de pago tienes?", "timestamp": "2026-09-08T15:07:51+00:00"},
        {"role": "assistant", "content": "Tenemos tres formas de pago.", "timestamp": "2026-09-08T15:08:16+00:00"},
        {"role": "assistant", "kind": "ui_component", "component_kind": "reaction", "content": "🤍",
         "timestamp": "2026-09-08T15:08:17+00:00"},
        {"role": "assistant", "content": "Hola de nuevo 🌿 Quedó pendiente lo del pedido 🤍",
         "timestamp": "2026-09-08T18:16:05+00:00"},
        {"role": "assistant", "content": "Tu pedido #29 ya va en camino 🚚", "timestamp": "2026-09-09T13:20:16+00:00"},
        {"role": "user", "content": "gracias", "timestamp": "2026-09-09T14:00:00+00:00"},
        {"role": "assistant", "content": "Con gusto.", "timestamp": "2026-09-09T14:00:20+00:00"},
    ]

    traj = build_legacy_trajectory(events, session_id="wa_100000000001", episode={"episode_id": "ep_012"})

    assert [t.sent_texts for t in traj.turns] == [("Tenemos tres formas de pago.",), ("Con gusto.",)]
    assert traj.turns[0].intents == ("reaction",)
