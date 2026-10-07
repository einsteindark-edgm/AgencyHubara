"""Informe de huecos (2026-10-07): cuántas veces pasa cada cosa que el análisis
propone corregir y si vale la pena corregirla.

Junta dos registros que ya viven en el vault de producción: el scorecard (cada
regla que falló, turno por turno, en `_evals/scorecards/`) y el testigo de
huecos (`_huecos/`, lo que el scorecard no ve). Solo cuenta: sin textos y sin
números completos de clientes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales_eval import huecos
from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.shared import turn_traces

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
DAY = "2026-10-09"


def _episode(vault: Path, sid: str, *, workflow: str, turns: int, fails=(), version: int = 8, day: str = DAY) -> None:
    for n in range(1, turns + 1):
        turn_traces.append_trace(vault, sid, {"episode_id": "ep_001", "turn": n, "workflow": workflow})
    store.append_scorecard(store.scorecards_dir(vault), {
        "mode": "turn",
        "registry_version": version,
        "session_id": sid,
        "episode_id": "ep_001",
        "by_turn": [{"turn": n} for n in range(1, turns + 1)],
        "results": [{"check_id": c, "verdict": "falla", "turn": t} for c, t in fails],
        "ts": f"{day}T20:00:00+00:00",
        "date": day,
    })


def _witness(vault: Path, sid: str, turn: int, holes: list[str], workflow: str = "v2") -> None:
    folder = vault / "_huecos"
    folder.mkdir(parents=True, exist_ok=True)
    line = {"at_ms": 0, "session": sid, "episode": "ep_001", "turn": turn, "workflow": workflow,
            "suelto": bool(holes), "huecos": holes}
    with (folder / f"{DAY}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _proposal(report: dict, pid: str) -> dict:
    return next(p for p in report["propuestas"] if p["id"] == pid)


def test_one_critical_case_is_worth_fixing_and_says_where_it_happened(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=4, fails=[("CON-03", 2)])
    _episode(tmp_path, "wa_100000000002", workflow="v1", turns=6)

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    gate = _proposal(report, "puerta_afirmaciones")
    assert gate["por_bot"]["nuevo"] == {"casos": 1, "conversaciones": 1, "turnos": 4}
    assert gate["por_bot"]["actual"] == {"casos": 0, "conversaciones": 0, "turnos": 6}
    assert gate["veredicto"] == "sí"
    assert gate["ejemplos"] == ["···0001 ep_001 t2"]


def test_a_major_hole_needs_frequency_and_nothing_seen_is_not_a_verdict(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=80, fails=[("VAR-06", 7)])

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    assert _proposal(report, "repregunta")["veredicto"] == "quizás"  # 1 caso en 80 turnos, 1 conversación
    assert _proposal(report, "despedida")["veredicto"] == "sin casos todavía"


def test_the_witness_counts_what_the_scorecard_cannot_see(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=3)
    _witness(tmp_path, "wa_100000000001", 1, [])
    _witness(tmp_path, "wa_100000000001", 2, ["texto_suelto_promete_volver"])

    loose = _proposal(huecos.build_report(tmp_path, days=3, now=NOW), "retener_texto_suelto")

    assert loose["por_bot"]["nuevo"]["casos"] == 1
    assert loose["testigo"] == {"texto_suelto_lista": 0, "texto_suelto_niega_foto": 0, "texto_suelto_promete_volver": 1}


def test_old_scorecards_and_days_outside_the_window_do_not_count(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=4, fails=[("CON-03", 2)], version=7)
    _episode(tmp_path, "wa_100000000002", workflow="v2", turns=4, fails=[("CON-03", 2)], day="2026-10-01")

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    assert _proposal(report, "puerta_afirmaciones")["por_bot"]["nuevo"]["casos"] == 0
    assert report["muestra"]["excluidos_version_vieja"] == 1


def test_what_step_two_fixed_is_watched_to_confirm_it_dropped(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v1", turns=5, fails=[("CIE-02", 4)])

    control = {c["regla"]: c for c in huecos.build_report(tmp_path, days=3, now=NOW)["control"]}

    assert control["CIE-02"]["casos"] == 1


def test_the_rendered_report_has_no_full_phone_numbers(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_109876544567", workflow="v2", turns=4, fails=[("CON-03", 2)])

    text = huecos.render(huecos.build_report(tmp_path, days=3, now=NOW))

    assert "109876544567" not in text and "···4567" in text
    assert "Puerta de afirmaciones" in text
