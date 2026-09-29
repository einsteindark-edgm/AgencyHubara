"""El bot nuevo (brazo B) en el sandbox se comporta como en producción.

Una auditoría independiente encontró que el brazo B del sandbox no era el
V2 + Jev de producción:

1. El historial del dashboard: en producción el ingest guarda cada mensaje
   del cliente ANTES del turno (`append_user_event`: texto, wamid, cita,
   hora); el sandbox lo cortaba antes de la ráfaga y nunca la agregaba. Las
   capacidades que leen el historial (`datos`, `item_del_pedido`, la ventana
   de contexto de jev-v3 con la cita del cliente) le preguntaban a Jev SIN el
   mensaje que el cliente acababa de mandar, y B bloqueaba datos por error.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe
from tests.plugins.chats.lab.test_lab_sandbox_materialize import T0, _case, _iso

ADDRESS = "Calle 45 # 12-30"
# El mensaje de la ráfaga como lo escribió el ingest de producción: el texto
# efectivo, la hora, el wamid y la foto del bot que el cliente citó.
ADDRESS_MSG = {
    "role": "user",
    "content": f"mi dirección es {ADDRESS}",
    "timestamp": _iso(T0 + 60_000),
    "wamid": "wamid.B",
    "reply_to": {"id": "wamid.BOT1", "author": "agent", "text": "Vela Buda en vaso"},
}
EVENTS = [
    {"role": "user", "content": "hola", "timestamp": _iso(T0 + 1_000)},
    {"role": "assistant", "content": "¡Buenas tardes! ¿Qué vela buscas?", "timestamp": _iso(T0 + 9_000)},
    ADDRESS_MSG,
    {"role": "assistant", "content": "Listo, quedó anotada", "timestamp": _iso(T0 + 75_000)},
]


def _address_case() -> dict:
    return _case(
        burst=[{"text": ADDRESS_MSG["content"], "ts_ms": T0 + 60_000, "wamid": "wamid.B"}],
        real={"inbound_text": ADDRESS_MSG["content"], "sent_texts": ["Listo, quedó anotada"]},
    )


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def new_bot_turn(tmp_path_factory) -> dict:
    """El brazo B con la ráfaga de la dirección: el LLM falso la guarda con
    `set_order_slot` (Jev falso: dice «sí la dio» solo si la conversación
    que ve nombra la dirección)."""
    return _run_probe(
        tmp_path_factory.mktemp("b"), _address_case(), arm="B", events=EVENTS,
        tool="set_order_slot", tool_args={"direccion": ADDRESS},
    )


def test_the_burst_is_in_the_dashboard_history_as_production_wrote_it(new_bot_turn: dict) -> None:
    result = new_bot_turn["result"]
    assert result["error"] is None, result
    sim = result["sim_session_id"]
    events = _jsonl(Path(new_bot_turn["sandbox"]) / "vault" / sim / "sessions" / f"{sim}.jsonl")

    customer = [e for e in events if e["role"] == "user"]
    assert customer == [EVENTS[0], ADDRESS_MSG]
    first_reply = next(i for i, e in enumerate(events) if e["role"] == "assistant" and i > 1)
    assert events.index(ADDRESS_MSG) < first_reply  # la ráfaga llegó ANTES del turno


def test_the_new_bot_keeps_the_address_the_customer_just_gave(new_bot_turn: dict) -> None:
    """`datos` (F6) le pregunta a Jev si el cliente dio la dirección: con la
    ráfaga en el historial, la ve y la dirección se guarda."""
    result = new_bot_turn["result"]
    sim = result["sim_session_id"]
    metadata = json.loads((Path(new_bot_turn["sandbox"]) / "vault" / sim / "metadata.json").read_text(encoding="utf-8"))

    slots = metadata["episodes"][-1]["order_draft"]["slots"]
    assert slots.get("direccion") == ADDRESS, slots
    assert not any("not_given_by_customer" in str(m.get("content")) for m in new_bot_turn["tool_messages"])


def test_jev_reads_the_customer_quote_in_the_turn_context(new_bot_turn: dict) -> None:
    """jev-v3 lee la cita del cliente de la ráfaga en el historial (F1)."""
    states = new_bot_turn["result"]["perception_states"]

    assert any("«Vela Buda en vaso»" in s for s in states), states


def test_a_handoff_summary_is_not_a_customer_message(tmp_path: Path) -> None:
    """El turno de handoff lo arranca remarketing: su resumen nunca pasó por
    el ingest. No se lee como si lo hubiera escrito el cliente (un «luego»
    del resumen pausaba la reactivación) ni queda en el historial."""
    summary = "[HANDOFF] el cliente dijo que luego te escribe"
    case = _case(trigger="handoff", burst=[], real={"inbound_text": summary, "sent_texts": ["Hola"]})

    report = _run_probe(tmp_path, case)

    result = report["result"]
    assert result["error"] is None, result
    assert result["readings"] == []
    sim = result["sim_session_id"]
    events = _jsonl(Path(report["sandbox"]) / "vault" / sim / "sessions" / f"{sim}.jsonl")
    assert not any("HANDOFF" in str(e.get("content")) for e in events if e["role"] == "user")
