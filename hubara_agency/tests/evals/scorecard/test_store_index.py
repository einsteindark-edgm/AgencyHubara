"""Calidad LLM sin 504 (2026-11-03): la lista, las gráficas y la alerta leen
un índice compacto de cada día de scorecards, no los registros completos.

En modo turno cada registro trae todos los checks de cada turno (`by_turn` y
`results`): un día puede pesar decenas de MB y la ventana es de 56 días. La
pestaña pedía cuatro listas a la vez, cada una parseaba todo y el cast de
Agents cortaba a los 15 s (504). Un día que no cambió no se vuelve a leer; lo
nuevo de un día que creció se lee solo desde donde quedó.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard import store

DAY = "2026-09-10"


def _card(session: str, episode: str, verdict: str, ts: str, **checks) -> dict:
    return {
        "session_id": session, "episode_id": episode, "verdict": verdict, "mode": "turn",
        "stage_final": "cierre", "turns": 2, "ts": ts,
        "by_turn": [{"turn": 1, "verdict": verdict, "results": []}],
        "results": [{"check_id": k, "verdict": v, "turn": 1, "source": "code"} for k, v in checks.items()],
    }


def _junk_keeping_size_and_mtime(path: Path, keep: bytes = b"") -> None:
    """Pisa el archivo con basura del mismo tamaño y le devuelve su fecha:
    quien lo vuelva a parsear ya no ve los registros."""
    st = path.stat()
    raw = path.read_bytes()
    junk = b"\n".join(line if line == keep else b"x" * len(line) for line in raw.split(b"\n"))
    assert len(junk) == st.st_size
    path.write_bytes(junk)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_a_day_that_did_not_change_is_not_parsed_again(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", f"{DAY}T10:00:00+00:00", **{"CON-01": "falla"}))
    store.append_scorecard(d, _card("wa_2", "ep_1", "PASA", f"{DAY}T11:00:00+00:00"))
    first = store.list_scorecards(d, dates=[DAY])

    _junk_keeping_size_and_mtime(d / f"{DAY}.jsonl")

    assert store.list_scorecards(d, dates=[DAY]) == first
    assert [(r["session_id"], r["checks"]) for r in first] == [("wa_1", {"CON-01": "falla"}), ("wa_2", {})]


def test_what_a_day_gains_after_the_index_shows_up(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", f"{DAY}T10:00:00+00:00"))
    assert [r["verdict"] for r in store.list_scorecards(d, dates=[DAY])] == ["PASA"]

    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", f"{DAY}T12:00:00+00:00"))
    store.append_scorecard(d, _card("wa_3", "ep_2", "ALERTA", f"{DAY}T13:00:00+00:00"))

    assert [(r["session_id"], r["verdict"]) for r in store.list_scorecards(d, dates=[DAY])] == [
        ("wa_1", "FALLA"), ("wa_3", "ALERTA"),
    ]


def test_a_rewritten_day_is_read_again(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", f"{DAY}T10:00:00+00:00"))
    store.list_scorecards(d, dates=[DAY])

    # Un script de reparación reescribe el día (más largo, otro contenido).
    (d / f"{DAY}.jsonl").unlink()
    store.append_scorecard(d, _card("wa_9", "ep_7", "ALERTA", f"{DAY}T09:00:00+00:00", **{"PAG-02": "falla"}))
    store.append_scorecard(d, _card("wa_8", "ep_1", "PASA", f"{DAY}T09:30:00+00:00"))

    assert sorted(r["session_id"] for r in store.list_scorecards(d, dates=[DAY])) == ["wa_8", "wa_9"]


def test_a_half_written_last_line_waits_until_it_is_complete(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", f"{DAY}T10:00:00+00:00"))
    path = d / f"{DAY}.jsonl"
    whole = path.read_text(encoding="utf-8")
    store.append_scorecard(d, _card("wa_2", "ep_1", "FALLA", f"{DAY}T11:00:00+00:00"))
    second = path.read_text(encoding="utf-8")[len(whole):]
    path.write_text(whole + second[:20], encoding="utf-8")  # el worker va por la mitad

    assert [r["session_id"] for r in store.list_scorecards(d, dates=[DAY])] == ["wa_1"]

    path.write_text(whole + second, encoding="utf-8")

    assert [r["session_id"] for r in store.list_scorecards(d, dates=[DAY])] == ["wa_2", "wa_1"]


def test_find_latest_reads_only_the_record_it_returns(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", f"{DAY}T10:00:00+00:00"))
    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", f"{DAY}T11:00:00+00:00", **{"CON-01": "falla"}))
    store.append_scorecard(d, _card("wa_2", "ep_1", "ALERTA", f"{DAY}T12:00:00+00:00"))
    store.list_scorecards(d, dates=[DAY])
    path = d / f"{DAY}.jsonl"
    target = path.read_bytes().split(b"\n")[1]

    _junk_keeping_size_and_mtime(path, keep=target)

    found = store.find_latest(d, "wa_1", "ep_1")
    assert found is not None and found["verdict"] == "FALLA"
    assert found["results"] == [{"check_id": "CON-01", "verdict": "falla", "turn": 1, "source": "code"}]


def test_the_latest_record_of_each_episode_of_a_conversation(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", "2026-09-10T10:00:00+00:00"))
    store.append_scorecard(d, _card("wa_2", "ep_1", "PASA", "2026-09-10T11:00:00+00:00"))
    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", "2026-09-12T10:00:00+00:00", **{"CON-01": "falla"}))
    store.append_scorecard(d, _card("wa_1", "ep_2", "ALERTA", "2026-09-11T10:00:00+00:00"))

    cards = store.latest_for_session(d, "wa_1")

    assert {ep: c["verdict"] for ep, c in cards.items()} == {"ep_1": "FALLA", "ep_2": "ALERTA"}
    assert cards["ep_1"]["results"][0]["check_id"] == "CON-01"  # el registro completo
    assert store.latest_for_session(d, "wa_9") == {}
