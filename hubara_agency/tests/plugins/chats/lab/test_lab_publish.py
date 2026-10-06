"""Lo que una corrida publica en `runs/<corrida>/` (plan §3.4 y contrato lab@v1).

La API de producción SOLO lee `runs/`: todo lo que la sección Laboratorio
muestra sale de acá. Con el PR 8 se publica el control real (A0): el hilo de
cada conversación con la salida real de cada turno, sus trazas, sus checks de
producción y el resumen con la MISMA forma que las gráficas de Calidad LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION
from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_lab.run.publish import publish_control
from src.sdk.labkit import FilesystemLabStore
from tests.plugins.chats.lab.test_lab_cases import SID, T0, WS, _bench

RUN = "run-20260923-a1b2"


def _published(tmp_path: Path) -> FilesystemLabStore:
    bench = _bench(tmp_path)
    cards = bench / "scorecards"
    cards.mkdir()
    record = {
        "session_id": SID, "episode_id": "ep_001", "verdict": "ALERTA", "ts": "2026-09-15T12:00:00+00:00",
        "date": "2026-09-15", "episode_date": "2026-09-15", "stage_final": "descubrimiento", "fidelity": "trace",
        "results": [
            {"check_id": "EST-08", "verdict": "falla", "level": "mayor", "turn": 2, "evidence": "catálogo", "critique": "", "source": "judge"},
            {"check_id": "APE-01", "verdict": "pasa", "level": "mayor", "turn": None, "evidence": "", "critique": "", "source": "code"},
        ],
    }
    older = dict(record, verdict="PASA", ts="2026-09-15T10:00:00+00:00", results=[])
    (cards / "2026-09-15.jsonl").write_text(json.dumps(older) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
    store = FilesystemLabStore(tmp_path / "bucket")
    publish_control(bench, build_cases(bench, sales_workspace=WS), store, run_id=RUN,
                    order={"arms": ["A1", "B"], "reps": 1, "bench_id": "bench-x"})
    return store


def _json(store, key):
    return json.loads(store.get_bytes(key))


def test_publishes_the_run_manifest_the_cases_and_the_bench_report(tmp_path: Path) -> None:
    store = _published(tmp_path)

    manifest = _json(store, f"runs/{RUN}/manifest.json")
    assert (manifest["run_id"], manifest["bench_id"], manifest["arms"]) == (RUN, "bench-x", ["A0", "A1", "B"])
    assert manifest["registry_version"] == REGISTRY_VERSION  # no se mezclan corridas de versiones distintas (§5.9)
    cases = store.get_bytes(f"runs/{RUN}/cases.jsonl").decode().splitlines()
    assert len(cases) == 2
    report = _json(store, f"runs/{RUN}/bench_report.json")
    assert report["counts"] == {"sessions": 1, "cases": 2, "excluded_turns": 1}
    assert report["exclusions"] == [{"id": f"{SID}/ep_001/t3", "reason": "turno_del_sistema"}]


def test_publishes_the_real_thread_with_the_control_output_per_turn(tmp_path: Path) -> None:
    thread = _json(_published(tmp_path), f"runs/{RUN}/threads/{SID}.json")

    assert thread["session_id"] == SID
    assert [m["content"] for m in thread["messages"]][:3] == ["hola", "¡Buenas tardes! Bienvenido", "me mandas el catálogo"]
    assert thread["messages"][5]["sender"] == "human"
    turn2 = next(t for t in thread["turns"] if t["turn"] == 2)
    assert turn2["outputs"]["A0"]["sent_texts"] == ["Claro, el envío sale en 12.900"]
    assert turn2["turn_key"] == "run:abc/t:2"
    assert [m["text"] for m in turn2["burst"]] == ["me mandas el catálogo", "y el envío a Bogotá"]


def test_publishes_the_control_traces_and_the_latest_production_checks(tmp_path: Path) -> None:
    store = _published(tmp_path)

    traces = [json.loads(line) for line in store.get_bytes(f"runs/{RUN}/turns/A0/0/{SID}.jsonl").decode().splitlines()]
    assert [t["turn"] for t in traces] == [1, 2]
    [score] = [json.loads(line) for line in store.get_bytes(f"runs/{RUN}/scores/A0/0/{SID}.jsonl").decode().splitlines()]
    assert score["verdict"] == "ALERTA"  # el último del episodio
    assert {r["check_id"] for r in score["results"]} == {"EST-08", "APE-01"}


def test_summary_has_the_same_shape_as_the_quality_charts(tmp_path: Path) -> None:
    summary = _json(_published(tmp_path), f"runs/{RUN}/summary.json")

    a0 = summary["arms"]["A0"]
    assert {"episodes", "verdicts", "pareto", "trend", "funnel"} <= set(a0)
    assert a0["verdicts"]["ALERTA"] == 1
    assert a0["pareto"][0]["check_id"] == "EST-08"
    assert summary["arms_pending"] == ["A1", "B"]


def test_publishes_a_conversation_index_with_the_control_verdict(tmp_path: Path) -> None:
    [row] = _json(_published(tmp_path), f"runs/{RUN}/conversations.json")

    assert row["session_id"] == SID
    assert row["turns"] == 2
    assert row["episodes"] == ["ep_001"]
    assert row["verdicts"] == {"A0": {"ep_001": "ALERTA"}}
    assert row["last_at_ms"] == T0 + 69_000
