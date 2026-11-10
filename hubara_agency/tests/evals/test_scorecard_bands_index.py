"""Las bandas leen un índice de cada día, no los scorecards completos
(memoria de la caja de app, 2026-11-05).

El barrido diario corre dentro del worker `sales_eval`, en la caja de 4 GB que
comparte con la API y otros 12 procesos. Para contar las fallas de 35 días
(7 recientes y 28 de base) leía cada registro completo de cada día y las
trazas completas de cada conversación, todo a la vez: el worker subió ~130 MB
y no los devolvió. Ahora cada día deja al lado un índice con lo que las
bandas cuentan (por regla, cuántos turnos se calificaron y cuáles fallaron,
con la evidencia ya recortada), y el bot de cada episodio sale del resumen de
las trazas. Un día que no cambió no se vuelve a leer.

Datos sintéticos: sesiones `wa_1000000000NN`, fechas inventadas.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.plugins.chats.agent.sales_eval import bands
from src.plugins.chats.agent.sales_eval.scorecard import store
from tests.evals.test_scorecard_bands import END, MAYOR, MENOR, NOW, _day, _history, _put, _rule


def _junk_keeping_size_and_mtime(path: Path) -> None:
    """Pisa el archivo con basura del mismo tamaño y le devuelve su fecha:
    quien lo vuelva a parsear ya no ve los registros."""
    st = path.stat()
    raw = path.read_bytes()
    junk = b"\n".join(b"x" * len(line) for line in raw.split(b"\n"))
    assert len(junk) == st.st_size
    path.write_bytes(junk)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_a_second_report_does_not_parse_unchanged_days_again(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 2}, recent_fails={MAYOR: 9, MENOR: 1})
    first = bands.build_report(tmp_path, now=NOW, end=END)
    assert first["muestra"]["episodios"] == 35
    assert _rule(first, MAYOR)["ejemplos"], "la regla que se movió trae sus ejemplos"

    for path in store.scorecards_dir(tmp_path).glob("*.jsonl"):
        _junk_keeping_size_and_mtime(path)
    for path in tmp_path.glob("*/evals/turn_traces.jsonl"):
        _junk_keeping_size_and_mtime(path)

    assert bands.build_report(tmp_path, now=NOW, end=END) == first


def test_what_a_day_gains_after_the_index_shows_up_is_counted(tmp_path: Path) -> None:
    _history(tmp_path, base_fails={MAYOR: 2}, recent_fails={MAYOR: 2})
    before = _rule(bands.build_report(tmp_path, now=NOW, end=END), MAYOR)

    _put(tmp_path, day=_day(0), turns=100, fails={MAYOR: 30}, session="wa_100000000002")
    after = _rule(bands.build_report(tmp_path, now=NOW, end=END), MAYOR)

    assert after["reciente"]["fallas"] == before["reciente"]["fallas"] + 30
    assert after["reciente"]["turnos"] == before["reciente"]["turnos"] + 100


def test_the_evidence_kept_in_the_index_is_already_redacted_and_cut(tmp_path: Path) -> None:
    long_evidence = "el cliente escribió desde el 3001234567 y " + "repreguntó la cantidad " * 20
    _history(tmp_path, base_fails={MAYOR: 2}, recent_fails={MAYOR: 9})
    _put(tmp_path, day=_day(1), turns=10, fails={MAYOR: 1}, session="wa_100000000003", evidence=long_evidence)

    examples = _rule(bands.build_report(tmp_path, now=NOW, end=END), MAYOR)["ejemplos"]
    index_text = "".join(p.read_text(encoding="utf-8") for p in (store.scorecards_dir(tmp_path) / "index").rglob("*.json"))

    assert "3001234567" not in index_text
    assert all(len(e["evidencia"]) <= 200 for e in examples)
