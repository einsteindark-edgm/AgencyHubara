"""Bandas por regla del scorecard (Ciclo Colossus F4, 2026-11-03).

Cada regla del scorecard tiene su tasa: fallas por cada 100 turnos calificados.
La de los últimos 7 días se compara con la de los 28 días anteriores (la
base). Una subida de 1σ se anota, de 2σ se diagnostica (el evaluador externo
la mira) y de 3σ abre una alerta; una regla crítica que nunca fallaba y ahora
falla también abre. Con poca base no se pasa de anotar.

Datos sintéticos: sesiones `wa_1000000000NN`, fechas inventadas, sin textos de
conversaciones reales.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.plugins.chats.agent.sales_eval import bands
from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID

END = date(2026, 10, 31)
NOW = datetime(2026, 10, 31, 4, 0, tzinfo=UTC)

MAYOR = "VAR-06"  # mayor, de código
CRITICA = "CIE-04"  # crítica, de código
MENOR = "EST-01"  # menor, de código
JUEZ = "DES-04"  # mayor, de juez: solo está donde el juez calificó


def _day(offset: int) -> str:
    """`offset` días antes del final de la ventana reciente (0 = END)."""
    return (END - timedelta(days=offset)).isoformat()


def _put(
    vault: Path,
    *,
    day: str,
    turns: int,
    fails: dict[str, int] | None = None,
    rules: tuple[str, ...] = (MAYOR, CRITICA, MENOR),
    session: str = "wa_100000000001",
    episode: str | None = None,
    evaluated: str | None = None,
    version: int = 11,
    mode: str = "turn",
    evidence: str = "repreguntó la cantidad",
) -> None:
    """Un episodio de `turns` turnos con su fila por regla y turno; las
    primeras `fails[regla]` filas de la regla fallan."""
    fails = fails or {}
    results = []
    for turn in range(1, turns + 1):
        for rule in rules:
            failed = turn <= fails.get(rule, 0)
            results.append({
                "check_id": rule,
                "verdict": "falla" if failed else "pasa",
                "level": SPECS_BY_ID[rule].level,
                "turn": turn,
                "evidence": evidence if failed else "",
            })
    store.append_scorecard(store.scorecards_dir(vault), {
        "mode": mode,
        "registry_version": version,
        "session_id": session,
        "episode_id": episode or f"ep_{day}",
        "by_turn": [{"turn": n} for n in range(1, turns + 1)],
        "results": results,
        "episode_date": day,
        "ts": f"{evaluated or day}T22:00:00+00:00",
        "date": evaluated or day,
    })


def _history(vault: Path, *, base_fails: dict[str, int], recent_fails: dict[str, int], turns: int = 100) -> None:
    """28 días de base y 7 recientes, un episodio de `turns` turnos por día."""
    for offset in range(7, 35):
        _put(vault, day=_day(offset), turns=turns, fails=base_fails)
    for offset in range(0, 7):
        _put(vault, day=_day(offset), turns=turns, fails=recent_fails)


def _rule(report: dict, rule_id: str) -> dict:
    return next(r for r in report["reglas"] if r["regla"] == rule_id)


def test_a_rule_far_above_its_baseline_opens_with_its_numbers(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})

    report = bands.build_report(tmp_path, now=NOW)

    row = _rule(report, MAYOR)
    assert row["banda"] == "abrir"
    assert row["motivo"] == "banda_3σ"
    assert row["reciente"] == {"fallas": 42, "turnos": 700, "tasa": 6.0}
    assert row["base"] == {"fallas": 28, "turnos": 2800, "tasa": 1.0, "dias": 28}
    assert row["desvio"] >= 3
    assert report["ventanas"]["reciente"] == {"desde": _day(6), "hasta": _day(0), "dias": 7}
    assert report["ventanas"]["base"] == {"desde": _day(34), "hasta": _day(7), "dias": 28}


def test_a_steady_rule_is_normal(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 1})

    row = _rule(bands.build_report(tmp_path, now=NOW), MAYOR)

    assert row["banda"] == "normal"
    assert row["reciente"]["tasa"] == row["base"]["tasa"] == 1.0


def test_one_sigma_is_noted_two_is_diagnosed_and_three_opens(tmp_path: Path) -> None:
    """Base de 1 por cada 100 turnos, igual todos los días: el σ de la tasa
    de 7 días (700 turnos) es el binomial, ≈0,38 por cada 100."""
    levels = {}
    for recent_total in (10, 13, 15):
        vault = tmp_path / str(recent_total)
        for offset in range(7, 35):
            _put(vault, day=_day(offset), turns=100, fails={MAYOR: 1})
        per_day = [recent_total // 7 + (1 if i < recent_total % 7 else 0) for i in range(7)]
        for offset, n in enumerate(per_day):
            _put(vault, day=_day(offset), turns=100, fails={MAYOR: n})
        levels[recent_total] = _rule(bands.build_report(vault, now=NOW), MAYOR)["banda"]

    assert levels == {10: "anotar", 13: "diagnosticar", 15: "abrir"}


def test_a_critical_rule_that_never_failed_opens_on_its_first_failure(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={}, recent_fails={})
    _put(tmp_path, day=_day(2), turns=3, fails={CRITICA: 1}, session="wa_100000000002", episode="ep_009")

    row = _rule(bands.build_report(tmp_path, now=NOW), CRITICA)

    assert row["banda"] == "abrir"
    assert row["motivo"] == "critica_nueva"
    assert row["base"]["fallas"] == 0 and row["reciente"]["fallas"] == 1


def test_a_couple_of_cases_of_a_new_non_critical_failure_are_diagnosed_not_opened(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={}, recent_fails={})
    _put(tmp_path, day=_day(1), turns=2, fails={MAYOR: 2}, session="wa_100000000003")

    row = _rule(bands.build_report(tmp_path, now=NOW), MAYOR)

    assert row["desvio"] >= 3
    assert row["banda"] == "diagnosticar"
    assert row["motivo"] == "pocos_casos"


def test_with_a_small_baseline_nothing_goes_above_noting(tmp_path: Path) -> None:
    for offset in (8, 9, 10):
        _put(tmp_path, day=_day(offset), turns=20)
    for offset in range(0, 7):
        _put(tmp_path, day=_day(offset), turns=100, fails={MAYOR: 9, CRITICA: 2})

    report = bands.build_report(tmp_path, now=NOW)

    for rule in (MAYOR, CRITICA):
        row = _rule(report, rule)
        assert (row["banda"], row["motivo"]) == ("anotar", "muestra_chica"), rule


def test_a_judge_rule_is_measured_only_on_the_turns_the_judge_saw(tmp_path: Path) -> None:
    """Con el juez apagado sus reglas no tienen filas: contar todos los turnos
    las diluiría."""
    for offset in range(0, 35):
        _put(tmp_path, day=_day(offset), turns=100)
    for offset in range(0, 7):
        _put(tmp_path, day=_day(offset), turns=10, rules=(JUEZ,), session="wa_100000000004", fails={JUEZ: 1})

    row = _rule(bands.build_report(tmp_path, now=NOW), JUEZ)

    assert row["reciente"] == {"fallas": 7, "turnos": 70, "tasa": 10.0}
    assert row["base"]["turnos"] == 0


def test_old_registry_versions_and_episode_mode_records_are_left_out(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={}, recent_fails={})
    _put(tmp_path, day=_day(1), turns=50, fails={CRITICA: 5}, session="wa_100000000005", version=7)
    _put(tmp_path, day=_day(1), turns=50, fails={CRITICA: 5}, session="wa_100000000006", mode="episode")

    report = bands.build_report(tmp_path, now=NOW)

    assert _rule(report, CRITICA)["reciente"]["fallas"] == 0
    assert report["muestra"]["excluidos_version_vieja"] == 1


def test_episodes_count_on_the_day_they_happened_not_the_day_they_were_scored(tmp_path: Path) -> None:
    """Un recálculo de hoy de un episodio de hace 20 días es base, no reciente."""
    _history(tmp_path, base_fails={}, recent_fails={})
    _put(tmp_path, day=_day(20), turns=10, fails={MAYOR: 4}, session="wa_100000000007", evaluated=_day(0))

    row = _rule(bands.build_report(tmp_path, now=NOW), MAYOR)

    assert row["base"]["fallas"] == 4
    assert row["reciente"]["fallas"] == 0


def test_examples_say_where_to_look_without_phones_or_customer_data(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={}, recent_fails={})
    _put(
        tmp_path, day=_day(1), turns=4, fails={CRITICA: 1}, session="wa_573001234567", episode="ep_012",
        evidence="escribió al 3001234567 y a ana@correo.com",
    )

    row = _rule(bands.build_report(tmp_path, now=NOW), CRITICA)
    dumped = json.dumps(row, ensure_ascii=False)

    assert row["ejemplos"] == [
        {"sesion": "…4567", "episodio": "ep_012", "turno": 1, "fecha": _day(1),
         "evidencia": row["ejemplos"][0]["evidencia"]},
    ]
    assert "3001234567" not in dumped and "573001234567" not in dumped and "ana@correo.com" not in dumped


def test_the_report_lists_every_rule_and_counts_each_band(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})

    report = bands.build_report(tmp_path, now=NOW)

    assert {r["regla"] for r in report["reglas"]} == set(SPECS_BY_ID)
    assert report["reglas"][0]["regla"] == MAYOR  # lo más grave primero
    assert report["conteo"]["abrir"] == 1
    assert sum(report["conteo"].values()) == len(SPECS_BY_ID)


def test_the_markdown_shows_only_what_moved(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})

    text = bands.render(bands.build_report(tmp_path, now=NOW))

    assert MAYOR in text and SPECS_BY_ID[MAYOR].name in text
    assert MENOR not in text


def test_the_cli_only_reads_unless_asked_to_alert(tmp_path: Path, monkeypatch, capsys) -> None:
    from src.plugins.chats.agent.sales_eval.evals import composition
    from src.plugins.chats.agent.sales_eval.scorecard import alerts

    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)
    created: list[str] = []

    class Tracker:
        async def find_open(self, fingerprint: str) -> int | None:
            return None

        async def create(self, title: str, body: str, labels: list[str]) -> int:
            created.append(title)
            return 9

        async def comment(self, number: int, body: str) -> None:  # pragma: no cover
            raise AssertionError("no hay alerta abierta")

    monkeypatch.setattr(alerts, "tracker_from_env", lambda: Tracker())
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    assert bands.main(["--json", "--hasta", END.isoformat()]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert _rule(printed, MAYOR)["banda"] == "abrir"
    assert created == []
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before  # no escribe nada

    assert bands.main(["--json", "--alert", "--hasta", END.isoformat()]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert created == [f"[ALR] Banda 3σ: {MAYOR} {SPECS_BY_ID[MAYOR].name}"]
    assert printed["alertas"] == [{"regla": MAYOR, "accion": "abierta", "issue": 9}]


def test_the_saved_report_is_the_record_of_the_day(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 1}, recent_fails={MAYOR: 6})
    report = bands.build_report(tmp_path, now=NOW)

    path = bands.save_report(tmp_path, report)

    assert path == tmp_path / "_evals" / "bands" / f"{END.isoformat()}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
