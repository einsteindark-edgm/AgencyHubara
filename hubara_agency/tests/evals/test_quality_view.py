"""Calidad LLM con la vista del laboratorio, sobre producción (2026-10-02).

El operador reemplaza la vista de Calidad LLM por la del laboratorio: cada
conversación real como un hilo, con cada turno del bot calificado, la ventana
del turno (resultado, paso a paso, decisiones de Jev) y el informe de Jev. Lo
que el laboratorio publica de su banco, producción lo arma del vault: el
mismo hilo (mismas ráfagas, `cases.turn_bursts`), las decisiones de Jev de
cada turno (`evals/decisions.jsonl`), el veredicto y el bot de cada episodio.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.plugins.chats.agent.sales.decisions.decision_log import decisions_path
from src.plugins.chats.agent.sales_eval import quality_view
from src.plugins.chats.shared import turn_traces

SID = "wa_573001234567"
OTHER = "wa_573009876543"


def _ms(hhmmss: str) -> int:
    return int(datetime.fromisoformat(f"2026-10-01T{hhmmss}+00:00").timestamp() * 1000)


def _iso(hhmmss: str) -> str:
    return datetime.fromtimestamp(_ms(hhmmss) / 1000, tz=timezone.utc).isoformat()


def _seed(vault: Path) -> None:
    sdir = vault / SID
    (sdir / "sessions").mkdir(parents=True)
    (sdir / "metadata.json").write_text(json.dumps({"episodes": [
        {"episode_id": "ep_001", "started_at_ms": _ms("15:00:00"), "llm_usage": {"cost_usd": 0.004}},
    ]}), encoding="utf-8")
    events = [
        {"role": "user", "content": "hola", "timestamp": _iso("15:00:00"), "wamid": "wamid.A", "kind": "text"},
        {"role": "assistant", "content": "¡Hola! ¿Qué buscas?", "timestamp": _iso("15:00:05"), "sender": "bot"},
        {"role": "user", "content": "velas de lavanda", "timestamp": _iso("15:01:00"), "wamid": "wamid.B", "kind": "text"},
        {"role": "assistant", "content": "Tengo estas", "timestamp": _iso("15:01:06"), "sender": "bot"},
    ]
    (sdir / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    perception = {"kind": "perception", "dur_ms": 400, "fallback": False, "cost_usd": 0.0002}
    for trace in (
        {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": _ms("15:00:01"), "workflow": "v2",
         "inbound": [{"seq": 1, "wamid": "wamid.A", "ts_ms": _ms("15:00:00"), "kind": "text", "text": "hola"}],
         "steps": [perception, {"kind": "verify", "decision": "send", "dur_ms": 300}]},
        {"episode_id": "ep_001", "turn": 2, "trigger": "complement", "turn_started_ms": _ms("15:00:30"), "workflow": "v2"},
        {"episode_id": "ep_001", "turn": 3, "trigger": "customer", "turn_started_ms": _ms("15:01:01"), "workflow": "v2",
         "inbound": [{"seq": 1, "wamid": "wamid.B", "ts_ms": _ms("15:01:00"), "kind": "text", "text": "velas de lavanda"}],
         "steps": [{**perception, "fallback": True, "dur_ms": 1600}]},
    ):
        turn_traces.append_trace(vault, SID, trace)


def _decisions(vault: Path, rows: list[dict]) -> None:
    path = decisions_path(vault, SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ── el hilo ────────────────────────────────────────────────────────────────


def test_the_thread_of_a_real_conversation_is_the_one_the_lab_shows(tmp_path: Path) -> None:
    _seed(tmp_path)

    thread = quality_view.production_thread(tmp_path, SID)

    assert thread["session_id"] == SID and [e["episode_id"] for e in thread["episodes"]] == ["ep_001"]
    assert [(m["role"], m["content"]) for m in thread["messages"]][:2] == [("user", "hola"), ("assistant", "¡Hola! ¿Qué buscas?")]
    # Un turno por respuesta al cliente (el complemento no es un turno del hilo).
    assert [(t["turn"], t["turn_key"]) for t in thread["turns"]] == [(1, f"{SID}/ep_001/t1"), (3, f"{SID}/ep_001/t3")]
    assert [m["wamid"] for m in thread["turns"][1]["burst"]] == ["wamid.B"]
    assert thread["turns"][0]["at_ms"] == _ms("15:00:01") and thread["turns"][0]["episode_id"] == "ep_001"


def test_a_conversation_without_traces_has_an_empty_thread(tmp_path: Path) -> None:
    thread = quality_view.production_thread(tmp_path, OTHER)

    assert (thread["session_id"], thread["messages"], thread["turns"]) == (OTHER, [], [])


# ── las decisiones de Jev de cada turno ─────────────────────────────────────


def test_each_decision_goes_to_its_turn(tmp_path: Path) -> None:
    _seed(tmp_path)
    _decisions(tmp_path, [
        {"at_ms": _ms("15:00:00"), "stage": "ingest", "message_id": "wamid.A", "capability": "compra", "by": "jev"},
        {"at_ms": _ms("15:00:02"), "stage": "turno", "capability": "persona", "by": "jev"},
        {"at_ms": _ms("15:00:31"), "stage": "turno", "capability": "destinatario", "by": "respaldo", "reason": "timeout"},
        {"at_ms": _ms("15:00:40"), "stage": "remarketing", "capability": "contactar", "by": "jev"},
        {"at_ms": _ms("15:00:59"), "stage": "ingest", "capability": "cupon", "by": "jev"},
        {"at_ms": _ms("15:01:02"), "stage": "turno", "capability": "monto", "by": "jev"},
    ])
    traces = turn_traces.read_traces(tmp_path, SID)
    rows = quality_view.read_session_decisions(tmp_path, SID)

    first = quality_view.turn_decisions(traces, rows, f"{SID}/ep_001/t1", session_id=SID)
    third = quality_view.turn_decisions(traces, rows, f"{SID}/ep_001/t3", session_id=SID)

    assert [(d["capability"], d["stage"], d.get("message")) for d in first] == [
        ("compra", "ingest", 1), ("persona", "turno", None), ("destinatario", "complemento", None),
    ]
    # Lo del remarketing no es de un turno de ventas; una lectura del ingest sin
    # mensaje va al turno que la siguió.
    assert [(d["capability"], d["stage"]) for d in third] == [("cupon", "ingest"), ("monto", "turno")]
    assert all("at_ms" not in d and "message_id" not in d for d in first + third)


def test_without_its_own_log_a_turn_shows_the_exit_decisions_of_its_trace(tmp_path: Path) -> None:
    """Trazas de antes del registro: el workflow nuevo dejaba la salida de Jev
    en la traza (`egress`)."""
    trace = {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": 1,
             "egress": {"verdicts": [{"capability": "rescate", "by": "jev", "provider": "jev", "value": "Hola", "rule": "Hola",
                                      "answers": [{"q": "egreso.rescate.1", "choice": "mensaje_al_cliente", "p": 0.9}]}]}}

    [decision] = quality_view.turn_decisions([trace], [], f"{SID}/ep_001/t1", session_id=SID)

    assert (decision["stage"], decision["capability"], decision["by"]) == ("turno", "rescate", "jev")
    assert "rule" not in decision  # compacta, como la del laboratorio


# ── la lista de conversaciones ──────────────────────────────────────────────


def test_each_conversation_lists_its_episodes_with_verdict_and_bot(tmp_path: Path) -> None:
    traces = {
        SID: [
            {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": 10, "workflow": "v2"},
            {"episode_id": "ep_002", "turn": 1, "trigger": "customer", "turn_started_ms": 50, "workflow": "v1"},
            {"episode_id": "ep_002", "turn": 2, "trigger": "ghost", "turn_started_ms": 60},
        ],
        OTHER: [{"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": 99}],
    }
    records = [
        {"session_id": SID, "episode_id": "ep_002", "verdict": "PASA"},
        {"session_id": SID, "episode_id": "ep_001", "verdict": "FALLA"},
        {"session_id": OTHER, "episode_id": "ep_001", "verdict": "ALERTA"},
    ]

    rows = quality_view.conversation_rows(records, traces)

    assert [r["session_id"] for r in rows] == [OTHER, SID]  # la más reciente primero
    row = rows[1]
    assert row["episodes"] == ["ep_001", "ep_002"] and row["turns"] == 2 and row["last_at_ms"] == 60
    assert row["verdicts"] == {"ep_001": "FALLA", "ep_002": "PASA"}
    assert row["bots"] == {"ep_001": "nuevo", "ep_002": "actual"}


# ── el informe de Jev ───────────────────────────────────────────────────────


def test_the_jev_report_measures_the_real_turns_like_the_lab(tmp_path: Path) -> None:
    _seed(tmp_path)
    _decisions(tmp_path, [
        {"at_ms": _ms("15:00:00"), "stage": "ingest", "capability": "compra", "by": "jev", "provider": "jev"},
        {"at_ms": _ms("15:01:02"), "stage": "turno", "capability": "monto", "by": "respaldo", "provider": "jev",
         "reason": "timeout"},
    ])

    report = quality_view.jev_report(tmp_path, [(SID, "ep_001")])

    assert (report["episodes"], report["turns"]) == (1, 2)
    assert report["perception"]["fallbacks"] == 1 and report["perception"]["turns"] == 2
    assert report["complement_rate"] == 0.5  # el turno 1 tuvo complemento
    assert report["decisions"]["jev_failed"] == 1 and report["decisions"]["jev_failed_by_reason"] == {"timeout": 1}
    # Costo por turno: el LLM del episodio repartido entre sus turnos + Jev.
    assert abs(report["cost_per_turn_usd"] - (0.004 + 0.0004) / 2) < 1e-9
