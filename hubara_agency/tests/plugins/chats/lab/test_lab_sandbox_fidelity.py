"""El bot nuevo (brazo B) en el sandbox se comporta como en producción.

Una auditoría independiente encontró que el brazo B del sandbox no era el
V2 + Jev de producción:

1. El historial del dashboard: en producción el ingest guarda cada mensaje
   del cliente ANTES del turno (`append_user_event`: texto, wamid, cita,
   hora); el sandbox lo cortaba antes de la ráfaga y nunca la agregaba. Las
   capacidades que leen el historial (`datos`, `item_del_pedido`, la ventana
   de contexto de jev-v3 con la cita del cliente) le preguntaban a Jev SIN el
   mensaje que el cliente acababa de mandar, y B bloqueaba datos por error.
2. Las notas del cupón y de lo que no existe en el catálogo: producción las
   decide en el ingest, mensaje por mensaje, con el motor y el bot de la
   conversación (`read_coupon_talk`, `read_catalog_gap`); el sandbox ponía el
   cupón siempre «en juego» y nunca armaba la nota de fuera de catálogo.
3. Lo que leen las lecturas: la foto, solo por el texto que el cliente puso
   en ella (el LLM sí ve la descripción).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe
from tests.plugins.chats.lab.test_lab_sandbox_materialize import T0, _case, _iso


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 1 · La ráfaga en el historial del dashboard ──────────────────────────────

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


# ── 2 · Cupón y fuera de catálogo ────────────────────────────────────────────

CATALOG = [{"id": "prod_cubo", "handle": "cubo-love", "title": "Cubo Love", "description": "Vela aromática con forma de cubo"}]
_PROMOTION = {"id": "promo_1", "code": "AMOR26", "discount_type": "percentage", "value": 10, "currency_code": "cop",
              "target_type": "items", "allocation": "each", "product_ids": ["prod_buda"], "status": "active"}
# El cupón ya aplicado al empezar el turno, solo para la Vela Buda (el
# borrador va en el Cubo Love: el pedido no está en un producto del cupón).
COUPON = {"code": "AMOR26", "promotion": _PROMOTION, "applied_at_ms": T0 + 30_000,
          "eligible_products": [{"title": "Vela Buda", "price_cop": 40_000, "discounted_price_cop": 36_000}]}
COUPON_NOT_IN_PLAY = "atiéndelo con el catálogo normal"
GAP_NOTE = "Lo que el cliente pidió o mostró y NO existe en el catálogo: «vaso»"


def _coupon_case(*texts: str) -> dict:
    burst = [{"text": t, "ts_ms": T0 + 60_000 + k * 1_000, "wamid": f"wamid.C{k}"} for k, t in enumerate(texts)]
    return _case(
        burst=burst,
        real={"inbound_text": "\n".join(texts), "sent_texts": ["Claro"]},
        episodes_at=[{"episode_id": "ep_001", "started_at_ms": T0, "closed_at_ms": None, "applied_coupon": COUPON}],
    )


@pytest.fixture(scope="module")
def current_bot_notes(tmp_path_factory) -> dict:
    """A1 (reglas): el cliente pregunta por algo que no existe y después por el
    envío; ninguno de los dos mensajes habla del cupón."""
    case = _coupon_case("¿la tienen en vaso?", "¿hacen envíos a Medellín?")
    return _run_probe(tmp_path_factory.mktemp("a1"), case, catalog=CATALOG)["result"]


def test_the_rule_names_what_the_catalog_does_not_have(current_bot_notes: dict) -> None:
    assert current_bot_notes["error"] is None, current_bot_notes
    assert any(GAP_NOTE in note for note in current_bot_notes["plugin_context"]), current_bot_notes["plugin_context"]


def test_a_message_that_does_not_talk_about_the_coupon_gets_the_short_note(current_bot_notes: dict) -> None:
    """Regla de hoy (`coupon_in_play`): ni el cupón ni sus productos → la nota
    lo recuerda en una línea y deja el turno al catálogo normal."""
    coupon_notes = [n for n in current_bot_notes["plugin_context"] if "CUPÓN APLICADO: AMOR26" in n]

    assert coupon_notes and all(COUPON_NOT_IN_PLAY in n for n in coupon_notes), coupon_notes


@pytest.fixture(scope="module")
def new_bot_coupon(tmp_path_factory) -> dict:
    """B con un mensaje que nombra el cupón: la regla dice que habla de él y
    Jev (el falso) dice que no."""
    case = _coupon_case("¿el cupón sirve para la Vela Buda?")
    return _run_probe(tmp_path_factory.mktemp("b-cupon"), case, arm="B", catalog=CATALOG)["result"]


def test_the_new_bot_asks_jev_whether_the_message_talks_about_the_coupon(new_bot_coupon: dict) -> None:
    """B decide con Jev (el falso dice «no» si la pregunta no aparece en lo
    que ve), aunque la regla diga que el mensaje nombra el cupón."""
    result = new_bot_coupon

    assert result["error"] is None, result
    coupon_notes = [n for n in result["plugin_context"] if "CUPÓN APLICADO: AMOR26" in n]
    assert coupon_notes and all(COUPON_NOT_IN_PLAY in n for n in coupon_notes), coupon_notes


# ── 3 · Lo que leen las lecturas ─────────────────────────────────────────────


def test_a_photo_reaches_the_llm_described_but_the_readings_only_read_its_caption(tmp_path: Path) -> None:
    """El LLM ve la descripción de la foto (el texto que reentró), como en
    producción; las lecturas de B no se la preguntan a Jev como si la hubiera
    escrito el cliente (sin texto en la foto, no hay nada que leer)."""
    photo = "[el cliente envió una foto: una vela con la frase «nos vemos mañana»]"
    case = _case(burst=[{"text": photo, "kind": "text", "caption": None, "ts_ms": T0 + 60_000, "wamid": "wamid.P_vision"}],
                 real={"inbound_text": photo, "sent_texts": ["Qué linda"]})

    result = _run_probe(tmp_path, case, arm="B")["result"]

    assert result["error"] is None, result
    assert photo in result["trace"]["inbound_text"]
    [verdicts] = result["readings"]
    assert all(v["reason"] == "no_question" and v["jev"] is None for v in verdicts if v["capability"] != "baja"), verdicts


# ── 4 · Las decisiones del motor viajan con el caso ──────────────────────────


def test_every_decision_of_the_new_bot_travels_with_its_case(new_bot_turn: dict) -> None:
    """Lo que el motor decidió en el ingest (por mensaje) y en el turno sale
    del sandbox con el caso, antes de que se borre."""
    decisions = new_bot_turn["result"]["decisions"]

    ingest = [d for d in decisions if d["stage"] == "ingest"]
    assert {"compra", "retoma", "cupon"} <= {d["capability"] for d in ingest}, ingest
    assert all(d["message"] == 1 and d["provider"] == "jev" for d in ingest), ingest
    [datos] = [d for d in decisions if d["capability"] == "datos"]
    assert (datos["stage"], datos["by"], datos["value"]) == ("turno", "jev", [])


def test_the_disagreements_of_the_case_travel_with_it(new_bot_coupon: dict) -> None:
    [cupon] = [d for d in new_bot_coupon["disagreements"] if d["capability"] == "cupon"]

    assert (cupon["rule"], cupon["jev"]) == (True, False)
    assert "¿el cupón sirve para la Vela Buda?" in cupon["state"]
    decision = next(d for d in new_bot_coupon["decisions"] if d["capability"] == "cupon")
    assert (decision["by"], decision["value"], decision["rule"]) == ("jev", False, True)
