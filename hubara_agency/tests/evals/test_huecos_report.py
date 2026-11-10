"""Informe de huecos (2026-10-07): cuántas veces pasa cada cosa que el análisis
propone corregir y si vale la pena corregirla.

Junta dos registros que ya viven en el vault de producción: el scorecard (cada
regla que falló, turno por turno, en `_evals/scorecards/`) y el testigo de
huecos (`_huecos/`, lo que el scorecard no ve). Solo cuenta: sin textos y sin
números completos de clientes.

Producción la atiende Colossus (workflow v2 con Jev, «nuevo»); Botsito (v1,
«actual») está retirado pero sigue en las ventanas largas. Por defecto solo
cuenta Colossus; `--bot todos` junta los dos para comparar.
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


def _episode(
    vault: Path, sid: str, *, workflow: str | tuple[str, ...], turns: int, fails=(), version: int = 8, day: str = DAY
) -> None:
    """`workflow` de cada turno: uno solo, o uno por turno (episodio mixto)."""
    flows = (workflow,) * turns if isinstance(workflow, str) else workflow
    for n in range(1, turns + 1):
        turn_traces.append_trace(vault, sid, {"episode_id": "ep_001", "turn": n, "workflow": flows[n - 1]})
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


def _witness(vault: Path, sid: str, turn: int, holes: list[str], workflow: str = "v2", day: str = DAY) -> None:
    folder = vault / "_huecos"
    folder.mkdir(parents=True, exist_ok=True)
    line = {"at_ms": 0, "session": sid, "episode": "ep_001", "turn": turn, "workflow": workflow,
            "suelto": bool(holes), "huecos": holes}
    with (folder / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _proposal(report: dict, pid: str) -> dict:
    return next(p for p in report["propuestas"] if p["id"] == pid)


def test_one_critical_case_is_worth_fixing_and_says_where_it_happened(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=4, fails=[("CON-03", 2)])
    _episode(tmp_path, "wa_100000000002", workflow="v1", turns=6)

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    gate = _proposal(report, "puerta_afirmaciones")
    assert gate["por_bot"] == {"nuevo": {"casos": 1, "conversaciones": 1, "turnos": 4}}
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
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=5, fails=[("CIE-02", 4)])

    control = {c["regla"]: c for c in huecos.build_report(tmp_path, days=3, now=NOW)["control"]}

    assert control["CIE-02"]["casos"] == 1


def test_the_rendered_report_has_no_full_phone_numbers(tmp_path: Path) -> None:
    _episode(tmp_path, "wa_109876544567", workflow="v2", turns=4, fails=[("CON-03", 2)])

    text = huecos.render(huecos.build_report(tmp_path, days=3, now=NOW))

    assert "109876544567" not in text and "···4567" in text
    assert "Puerta de afirmaciones" in text


def test_a_failure_only_botsito_had_is_not_worth_fixing(tmp_path: Path) -> None:
    """Una regla crítica con un solo caso vale la pena, pero si el caso es del
    bot retirado no es de producción: no decide nada."""
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=60)
    _episode(tmp_path, "wa_100000000002", workflow="v1", turns=60, fails=[("TAG-02", 3)])

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    pay = _proposal(report, "motivo_verificacion_pago")
    assert pay["veredicto"] == "sin casos todavía"
    assert pay["reglas"] == {"TAG-02": 0}
    assert pay["ejemplos"] == []
    assert pay["por_bot"] == {"nuevo": {"casos": 0, "conversaciones": 0, "turnos": 60}}
    assert report["bot"] == "nuevo"


def _both_bots(vault: Path) -> None:
    """Dos conversaciones de Colossus, una de Botsito y una mixta, con las
    mismas reglas fallando, y el testigo de los dos bots."""
    _episode(vault, "wa_100000000001", workflow="v2", turns=30, fails=[("CON-03", 2), ("CIE-02", 5), ("VAR-06", 7)])
    _episode(vault, "wa_100000000002", workflow="v2", turns=30, fails=[("CON-03", 4), ("POS-01", 4)])
    _episode(vault, "wa_100000000003", workflow="v1", turns=40,
             fails=[("CON-03", 1), ("CON-03", 2), ("CIE-02", 3), ("VAR-06", 1), ("TAG-02", 6)])
    _episode(vault, "wa_100000000004", workflow=("v1", "v2"), turns=2, fails=[("CON-03", 1)])
    _witness(vault, "wa_100000000001", 3, ["texto_suelto_lista"])
    _witness(vault, "wa_100000000002", 1, [])
    _witness(vault, "wa_100000000003", 1, ["texto_suelto_lista"], workflow="v1")
    _witness(vault, "wa_100000000003", 2, ["texto_suelto_niega_foto"], workflow="v1")


def test_by_default_only_colossus_counts_and_its_numbers_are_exact(tmp_path: Path) -> None:
    _both_bots(tmp_path)

    report = huecos.build_report(tmp_path, days=3, now=NOW)

    assert report["bot"] == "nuevo"
    gate = _proposal(report, "puerta_afirmaciones")
    assert gate["reglas"] == {"CON-03": 2, "POS-01": 1, "ENV-05": 0, "DES-10": 0, "DES-05": 0, "EST-05": 0}
    assert gate["por_bot"] == {"nuevo": {"casos": 2, "conversaciones": 2, "turnos": 60}}
    assert gate["motivo"] == "regla crítica: 2 caso(s) en 60 turnos (3.3 por cada 100), 2 conversación(es)"
    assert gate["ejemplos"] == ["···0001 ep_001 t2", "···0002 ep_001 t4"]
    assert _proposal(report, "motivo_verificacion_pago")["veredicto"] == "sin casos todavía"
    loose = _proposal(report, "retener_texto_suelto")
    assert loose["testigo"] == {"texto_suelto_lista": 1, "texto_suelto_niega_foto": 0, "texto_suelto_promete_volver": 0}
    assert loose["por_bot"] == {"nuevo": {"casos": 1, "conversaciones": 1, "turnos": 2}}
    control = {c["regla"]: c for c in report["control"]}
    assert control["CIE-02"] == {
        "regla": "CIE-02", "nombre": control["CIE-02"]["nombre"], "casos": 1, "por_bot": {"nuevo": 1},
        "ejemplos": ["···0001 ep_001 t5"],
    }
    assert report["muestra"] == {
        "episodios": 2, "excluidos_version_vieja": 0, "excluidos_otro_bot": 2,
        "turnos": {"nuevo": 60}, "turnos_testigo": {"nuevo": 2},
    }


def test_bot_todos_keeps_the_combined_numbers_for_comparing(tmp_path: Path) -> None:
    _both_bots(tmp_path)

    report = huecos.build_report(tmp_path, days=3, now=NOW, bot=None)

    assert report["bot"] == "todos"
    gate = _proposal(report, "puerta_afirmaciones")
    assert gate["reglas"]["CON-03"] == 5 and gate["reglas"]["POS-01"] == 1
    assert gate["por_bot"] == {
        "nuevo": {"casos": 2, "conversaciones": 2, "turnos": 60},
        "actual": {"casos": 3, "conversaciones": 2, "turnos": 42},  # el mixto va con Botsito
    }
    assert gate["motivo"] == "regla crítica: 5 caso(s) en 102 turnos (4.9 por cada 100), 4 conversación(es)"
    assert _proposal(report, "motivo_verificacion_pago")["veredicto"] == "sí"
    loose = _proposal(report, "retener_texto_suelto")
    assert loose["testigo"] == {"texto_suelto_lista": 2, "texto_suelto_niega_foto": 1, "texto_suelto_promete_volver": 0}
    control = {c["regla"]: c for c in report["control"]}
    assert (control["CIE-02"]["casos"], control["CIE-02"]["por_bot"]) == (2, {"nuevo": 1, "actual": 1})
    assert report["muestra"] == {
        "episodios": 4, "excluidos_version_vieja": 0, "excluidos_otro_bot": 0,
        "turnos": {"nuevo": 60, "actual": 42}, "turnos_testigo": {"nuevo": 2, "actual": 2},
    }


def test_bot_actual_looks_only_at_botsito(tmp_path: Path) -> None:
    _both_bots(tmp_path)

    report = huecos.build_report(tmp_path, days=3, now=NOW, bot="actual")

    assert report["bot"] == "actual"
    assert _proposal(report, "puerta_afirmaciones")["por_bot"] == {
        "actual": {"casos": 3, "conversaciones": 2, "turnos": 42}
    }
    assert report["muestra"]["excluidos_otro_bot"] == 2


def test_the_markdown_says_which_bot_it_counts(tmp_path: Path) -> None:
    _both_bots(tmp_path)

    only = huecos.render(huecos.build_report(tmp_path, days=3, now=NOW))
    both = huecos.render(huecos.build_report(tmp_path, days=3, now=NOW, bot=None))

    assert "Colossus" in only and "Botsito" not in only
    assert "Colossus" in both and "Botsito" in both


def test_the_cli_counts_the_production_bot_unless_asked(tmp_path: Path, monkeypatch, capsys) -> None:
    from src.plugins.chats.agent.sales_eval.evals import composition

    today = datetime.now(UTC).date().isoformat()
    _episode(tmp_path, "wa_100000000001", workflow="v2", turns=10, day=today)
    _episode(tmp_path, "wa_100000000002", workflow="v1", turns=10, fails=[("TAG-02", 2)], day=today)
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)

    def run(*flags: str) -> dict:
        assert huecos.main(["--json", "--dias", "1", *flags]) == 0
        return json.loads(capsys.readouterr().out)

    default, todos = run(), run("--bot", "todos")
    assert default["bot"] == "nuevo"
    assert _proposal(default, "motivo_verificacion_pago")["veredicto"] == "sin casos todavía"
    assert todos["bot"] == "todos"
    assert _proposal(todos, "motivo_verificacion_pago")["veredicto"] == "sí"
    assert run("--bot", "actual")["muestra"]["turnos"] == {"actual": 10}
