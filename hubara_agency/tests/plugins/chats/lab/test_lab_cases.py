"""Armado de casos del laboratorio (plan §0 y PR 8): un caso por turno real.

"Teacher forcing": cada bot responde cada turno sobre el PREFIJO REAL de la
conversación, sin inventar al cliente. Un caso lleva:
  * cuántos eventos del historial del dashboard y cuántos mensajes del
    historial del LLM había ANTES del turno (el sandbox los trunca ahí);
  * la ráfaga: los mensajes del cliente desde la última respuesta del bot;
  * el estado del momento: etapa, borrador y estado de la traza, y los
    episodios como estaban al empezar el turno;
  * la salida real del bot (A0), que es el control.
Los turnos del sistema (ghosting) no son casos: van a exclusiones con motivo.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_lab.cases import build_cases

SID = "wa_573001234567"
WS = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
T0 = 1_789_500_000_000  # después del corte
CUT = 1_789_000_000_000


def _iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _bench(tmp_path: Path) -> Path:
    b = tmp_path / "bench"
    s = b / "vault" / SID
    (s / "sessions").mkdir(parents=True)
    (s / "evals").mkdir()
    episodes = [
        {"episode_id": "ep_001", "started_at_ms": T0, "closed_at_ms": T0 + 600_000, "closing_tag": "RECHAZO"},
        {"episode_id": "ep_002", "started_at_ms": T0 + 3_600_000, "closed_at_ms": None},
    ]
    (s / "metadata.json").write_text(json.dumps({"episodes": episodes, "tag": "INTERESADO"}), encoding="utf-8")
    events = [
        {"role": "user", "content": "hola", "timestamp": _iso(T0 + 1_000)},
        {"role": "assistant", "content": "¡Buenas tardes! Bienvenido", "timestamp": _iso(T0 + 9_000)},
        {"role": "user", "content": "me mandas el catálogo", "timestamp": _iso(T0 + 60_000)},
        {"role": "user", "content": "y el envío a Bogotá", "timestamp": _iso(T0 + 67_000)},
        {"role": "assistant", "content": "Claro, el envío sale en 12.900", "timestamp": _iso(T0 + 75_000)},
        {"role": "assistant", "content": "te escribe una persona del equipo", "sender": "human", "timestamp": _iso(T0 + 3_700_000)},
        {"role": "user", "content": "ok gracias", "timestamp": _iso(T0 + 3_800_000)},
    ]
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    traces = [
        {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 3_000,
         "inbound_text": "hola", "sent_texts": ["¡Buenas tardes! Bienvenido"], "stage_in": "descubrimiento",
         "draft": {}, "state": {"tag": None, "route": "ventas"}, "tools": [], "guards": []},
        {"turn": 2, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 69_000,
         "inbound_text": "me mandas el catálogo\ny el envío a Bogotá", "sent_texts": ["Claro, el envío sale en 12.900"],
         "stage_in": "descubrimiento", "draft": {"producto": "cubo-love"}, "state": {"tag": "INTERESADO"},
         "tools": [{"name": "send_shipping_rates", "ok": True}], "guards": [], "turn_key": "run:abc/t:2"},
        {"turn": 3, "episode_id": "ep_001", "trigger": "ghost", "turn_started_ms": T0 + 400_000,
         "inbound_text": "[SISTEMA] ghosting", "sent_texts": []},
        {"turn": 1, "episode_id": "ep_000", "trigger": "customer", "turn_started_ms": CUT - 5_000,
         "inbound_text": "viejo", "sent_texts": ["viejo"]},
    ]
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    llm = b / "agent_state" / WS / "sessions"
    llm.mkdir(parents=True)
    lines = [
        {"_type": "metadata", "key": SID, "last_consolidated": 0, "metadata": {}},
        {"role": "user", "content": "hola", "timestamp": _iso(T0 + 8_000)},
        {"role": "assistant", "content": "¡Buenas tardes!", "timestamp": _iso(T0 + 8_000)},
        {"role": "user", "content": "me mandas el catálogo\ny el envío a Bogotá", "timestamp": _iso(T0 + 74_000)},
        {"role": "assistant", "content": "", "tool_calls": [], "timestamp": _iso(T0 + 74_000)},
    ]
    (llm / f"{SID}.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    (b / "manifest.json").write_text(json.dumps({"bench_id": "bench-x", "sessions": [SID], "since_ms": CUT}), encoding="utf-8")
    return b


def _case(cases, turn: int):
    return next(c for c in cases if c.turn == turn and c.episode_id == "ep_001")


def test_one_case_per_customer_turn_since_the_cut(tmp_path: Path) -> None:
    result = build_cases(_bench(tmp_path), sales_workspace=WS)

    assert [(c.episode_id, c.turn) for c in result.cases] == [("ep_001", 1), ("ep_001", 2)]
    assert result.exclusions == ((f"{SID}/ep_001/t3", "turno_del_sistema"),)


def test_case_carries_the_burst_and_the_real_prefix_lengths(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 2)

    assert [m["text"] for m in case.burst] == ["me mandas el catálogo", "y el envío a Bogotá"]
    assert [m["ts_ms"] for m in case.burst] == [T0 + 60_000, T0 + 67_000]
    assert case.dashboard_prefix == 2  # hola + bienvenida
    assert case.llm_prefix == 2  # los mensajes del turno 1 (sin la línea de metadata)
    assert case.turn_key == "run:abc/t:2"
    assert case.case_id == f"{SID}/ep_001/t2"


def test_case_carries_the_state_after_the_turn_for_the_scorecard(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 2)

    # `draft` y `state` son los de la traza del MISMO turno: el estado al
    # TERMINAR el turno (lo que evalúa el scorecard), no el de su inicio.
    assert (case.stage_in, case.draft, case.state["tag"]) == ("descubrimiento", {"producto": "cubo-love"}, "INTERESADO")


def test_case_carries_the_state_before_the_turn_from_the_previous_trace(tmp_path: Path) -> None:
    """El sandbox arranca el turno con el estado de su INICIO: el que dejó la
    traza anterior. Usar el de la misma traza metería información del futuro
    (el borrador que el bot llenó en ese turno)."""
    cases = build_cases(_bench(tmp_path), sales_workspace=WS).cases
    first, second = _case(cases, 1), _case(cases, 2)

    assert first.first_in_episode is True
    assert first.draft_before == {}
    empty = {"tag": None, "route": None, "escalation_reason": None, "closing_tag": None, "order_id": None}
    assert first.state_before == empty  # la traza anterior (ep_000, antes del corte) no trae estado
    assert second.first_in_episode is False
    assert second.draft_before == {}  # el turno 1 no había llenado nada
    assert second.state_before == empty | {"route": "ventas"}


def test_session_state_before_comes_from_the_previous_episode_too(tmp_path: Path) -> None:
    """El tag y la ruta son de la sesión: al primer turno de un episodio los
    trae la última traza del episodio anterior; lo del episodio (cierre,
    orden) no pasa de un episodio al otro."""
    b = _bench(tmp_path)
    traces_path = b / "vault" / SID / "evals" / "turn_traces.jsonl"
    extra = {"turn": 1, "episode_id": "ep_002", "trigger": "customer", "turn_started_ms": T0 + 3_800_500,
             "inbound_text": "ok gracias", "sent_texts": ["con gusto"], "draft": {}, "state": {"tag": "RECHAZO"}}
    traces_path.write_text(traces_path.read_text(encoding="utf-8") + json.dumps(extra) + "\n", encoding="utf-8")

    case = next(c for c in build_cases(b, sales_workspace=WS).cases if c.episode_id == "ep_002")

    assert case.first_in_episode is True
    assert case.state_before["tag"] is None  # la última traza anterior es el ghost del ep_001 (sin state)
    assert case.draft_before == {}


def test_case_carries_the_episodes_of_that_moment(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 2)

    [episode] = case.episodes_at
    assert episode["episode_id"] == "ep_001"
    assert episode["closed_at_ms"] is None and "closing_tag" not in episode  # a esa hora seguía abierto


def test_case_keeps_the_real_output_as_the_control(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 2)

    assert case.real["sent_texts"] == ["Claro, el envío sale en 12.900"]
    assert case.real["tools"] == [{"name": "send_shipping_rates", "ok": True}]


def test_turn_key_is_synthesized_for_v1_traces(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 1)

    assert case.turn_key == f"{SID}/ep_001/t1"


def test_cases_are_json_serializable(tmp_path: Path) -> None:
    for case in build_cases(_bench(tmp_path), sales_workspace=WS).cases:
        json.dumps(case.to_dict())


def test_the_burst_keeps_the_raw_text_the_classifier_reads(tmp_path: Path) -> None:
    """El ingest le agrega al turno la campaña citada o el episodio anterior,
    pero el clasificador (Jev) lee el texto crudo del cliente. La traza nueva
    lo guarda (`raw_text`); en las viejas sale del dashboard (el mensaje del
    cliente, con el mismo wamid) cuando el turno lo trae envuelto. Así el
    laboratorio le da a Jev lo mismo que producción."""
    b = _bench(tmp_path)
    s = b / "vault" / SID
    events = [json.loads(line) for line in (s / "sessions" / f"{SID}.jsonl").read_text(encoding="utf-8").splitlines()]
    events[2]["wamid"], events[3]["wamid"] = "wamid.A", "wamid.B"
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    traces = [json.loads(line) for line in (s / "evals" / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
    traces[1]["inbound"] = [
        {"seq": 1, "wamid": "wamid.A", "ts_ms": T0 + 60_000, "kind": "text",
         "text": "[respondes a la campaña] me mandas el catálogo", "raw_text": "me mandas el catálogo"},
        {"seq": 2, "wamid": "wamid.B", "ts_ms": T0 + 67_000, "kind": "text",
         "text": "[episodio anterior: compró un Cubo Love]\ny el envío a Bogotá"},
    ]
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")

    case = _case(build_cases(b, sales_workspace=WS).cases, 2)

    assert [m["text"] for m in case.burst] == [
        "[respondes a la campaña] me mandas el catálogo", "[episodio anterior: compró un Cubo Love]\ny el envío a Bogotá",
    ]
    assert [m.get("raw_text") for m in case.burst] == ["me mandas el catálogo", "y el envío a Bogotá"]


PHOTO = '[el cliente envió una foto: una vela con la frase «nos vemos mañana»] con el texto: "¿la tienen en rojo?"'
BUTTON = "[el cliente tocó el botón: ✅ Confirmar]"
CART = "[el cliente armó un carrito con: 2× HUB-CUBO-01, 1× HUB-BUDA-02]"


def _with_burst(tmp_path: Path, contents: list[str], *, inbound: list[dict] | None = None) -> Path:
    """El banco con la ráfaga del turno 2 cambiada (y, con `inbound`, traza v2)."""
    b = _bench(tmp_path)
    s = b / "vault" / SID
    events = [json.loads(line) for line in (s / "sessions" / f"{SID}.jsonl").read_text(encoding="utf-8").splitlines()]
    burst_events = [{"role": "user", "content": c, "timestamp": _iso(T0 + 60_000 + k * 1_000)} for k, c in enumerate(contents)]
    events = [*events[:2], *burst_events, *events[4:]]
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    if inbound is not None:
        traces = [json.loads(line) for line in (s / "evals" / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
        traces[1]["inbound"] = inbound
        (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    return b


EXPECTED_FIELDS = [
    {"kind": "text", "caption": "¿la tienen en rojo?"},
    {"kind": "interactive", "interactive": {"type": "button_reply", "id": "", "title": "✅ Confirmar"}},
    {"kind": "order", "order": {"product_items": [{"product_retailer_id": "HUB-CUBO-01", "quantity": 2},
                                                   {"product_retailer_id": "HUB-BUDA-02", "quantity": 1}]}},
]


def _fields(message: dict) -> dict:
    return {k: message[k] for k in ("kind", "caption", "interactive", "order") if k in message}


def test_the_burst_carries_what_the_webhook_brought_besides_the_text(tmp_path: Path) -> None:
    """Las lecturas del ingest leen lo que el cliente ESCRIBIÓ: en una foto,
    solo el texto que puso en ella; un botón o un carrito los lee el código.
    La traza v2 guarda el tipo del mensaje (`kind`: la foto reentra como
    `text`); el resto sale del texto efectivo que armó el ingest."""
    inbound = [
        {"seq": 1, "wamid": "wamid.P_vision", "ts_ms": T0 + 60_000, "kind": "text", "text": PHOTO},
        {"seq": 2, "wamid": "wamid.K", "ts_ms": T0 + 61_000, "kind": "interactive", "text": BUTTON},
        {"seq": 3, "wamid": "wamid.O", "ts_ms": T0 + 62_000, "kind": "order", "text": CART},
    ]
    b = _with_burst(tmp_path, [PHOTO, BUTTON, CART], inbound=inbound)

    case = _case(build_cases(b, sales_workspace=WS).cases, 2)

    assert [_fields(m) for m in case.burst] == EXPECTED_FIELDS
    assert [m["text"] for m in case.burst] == [PHOTO, BUTTON, CART]  # el LLM sigue viendo el texto efectivo


def test_old_traces_infer_the_kind_from_the_text_the_ingest_wrote(tmp_path: Path) -> None:
    """Traza v1 (ráfaga desde el dashboard): el tipo sale del texto efectivo."""
    b = _with_burst(tmp_path, [PHOTO, BUTTON, CART])

    case = _case(build_cases(b, sales_workspace=WS).cases, 2)

    assert [_fields(m) for m in case.burst] == EXPECTED_FIELDS


def _v1_bench(tmp_path: Path, events: list[dict], traces: list[dict]) -> Path:
    """El banco con otro historial y otras trazas v1 (sin `inbound[]`)."""
    b = _bench(tmp_path)
    s = b / "vault" / SID
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    return b


def _v1_trace(turn: int, started: int, inbound_text: str) -> dict:
    return {"turn": turn, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": started,
            "inbound_text": inbound_text, "sent_texts": ["ok"], "tools": [], "guards": []}


HOLA = {"role": "user", "content": "hola", "timestamp": _iso(T0 + 1_000)}
BIENVENIDA = {"role": "assistant", "content": "¡Buenas tardes! Bienvenido", "timestamp": _iso(T0 + 9_000)}
CATALOGO = {"role": "user", "content": "me mandas el catálogo", "timestamp": _iso(T0 + 60_000)}
# Llega MIENTRAS el bot contesta el turno 2 (antes de su respuesta): el
# workflow lo responde en un turno 3 aparte (ráfaga partida en dos turnos).
ENVIO = {"role": "user", "content": "y el envío a Bogotá", "timestamp": _iso(T0 + 70_000)}
RESPUESTA_T2 = {"role": "assistant", "content": "Claro, te comparto el catálogo", "timestamp": _iso(T0 + 75_000)}


def test_a_message_sent_while_the_bot_answered_is_the_burst_of_the_next_turn(tmp_path: Path) -> None:
    """Traza v1 de una ráfaga partida: el turno 3 responde un mensaje que llegó
    ANTES de la respuesta del turno 2. El caso lo trae igual (lo dice la
    traza real, `inbound_text`); antes quedaba sin ráfaga y el bot simulado
    respondía un mensaje vacío."""
    b = _v1_bench(
        tmp_path,
        [HOLA, BIENVENIDA, CATALOGO, ENVIO, RESPUESTA_T2],
        [_v1_trace(1, T0 + 3_000, "hola"), _v1_trace(2, T0 + 62_000, "me mandas el catálogo"),
         _v1_trace(3, T0 + 80_000, "y el envío a Bogotá")],
    )

    cases = build_cases(b, sales_workspace=WS).cases

    assert [m["text"] for m in _case(cases, 2).burst] == ["me mandas el catálogo"]
    third = _case(cases, 3)
    assert [m["text"] for m in third.burst] == ["y el envío a Bogotá"]
    assert third.dashboard_prefix == 3  # hola, bienvenida y el catálogo: lo que había cuando llegó el mensaje
    # La respuesta del turno 2 salió DESPUÉS del mensaje y ANTES del turno 3:
    # el sandbox la pone en el historial en su lugar.
    assert [e["content"] for e in third.dashboard_between] == ["Claro, te comparto el catálogo"]


def test_a_message_the_running_turn_absorbed_is_part_of_its_burst(tmp_path: Path) -> None:
    """Traza v1 de un turno que absorbió un mensaje llegado después de
    empezar (la traza real lo trae en `inbound_text`): va en SU ráfaga y no
    en la del turno siguiente."""
    b = _v1_bench(
        tmp_path,
        [HOLA, BIENVENIDA, CATALOGO, ENVIO, RESPUESTA_T2],
        [_v1_trace(1, T0 + 3_000, "hola"), _v1_trace(2, T0 + 62_000, "me mandas el catálogo\ny el envío a Bogotá")],
    )

    case = _case(build_cases(b, sales_workspace=WS).cases, 2)

    assert [m["text"] for m in case.burst] == ["me mandas el catálogo", "y el envío a Bogotá"]
    assert case.dashboard_between == []


def test_photos_that_reentered_after_the_vision_are_in_the_burst_in_any_order(tmp_path: Path) -> None:
    """Las fotos entran al turno cuando la visión termina (el dashboard las
    guarda ya con el turno en marcha) y en la traza v1 van en otro orden que
    en el dashboard. La ráfaga las trae todas, en el orden del dashboard."""
    foto_a = {"role": "user", "content": "[el cliente envió una foto: vela lila]", "timestamp": _iso(T0 + 60_000)}
    texto = {"role": "user", "content": "me gustan esas?", "timestamp": _iso(T0 + 60_800)}
    foto_b = {"role": "user", "content": "[el cliente envió una foto: vela azul]", "timestamp": _iso(T0 + 61_500)}
    segundo = {**_v1_trace(2, T0 + 60_200, f"{foto_a['content']}\n{foto_b['content']}\n{texto['content']}"),
               "recorded_at_ms": T0 + 70_000}
    b = _v1_bench(tmp_path, [HOLA, BIENVENIDA, foto_a, texto, foto_b], [_v1_trace(1, T0 + 3_000, "hola"), segundo])

    case = _case(build_cases(b, sales_workspace=WS).cases, 2)

    assert [m["text"] for m in case.burst] == [foto_a["content"], texto["content"], foto_b["content"]]
