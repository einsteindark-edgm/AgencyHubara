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


def test_case_carries_the_state_of_that_moment(tmp_path: Path) -> None:
    case = _case(build_cases(_bench(tmp_path), sales_workspace=WS).cases, 2)

    assert (case.stage_in, case.draft, case.state["tag"]) == ("descubrimiento", {"producto": "cubo-love"}, "INTERESADO")
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
