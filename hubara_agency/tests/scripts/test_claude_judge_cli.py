"""`scripts/claude_judge.py`: lo que Claude Code usa para calificar la cola del
juez en la caja de producción, por SSM (salida de SSM ≈ 24.000 caracteres).

- `siguiente` trae prompts ENTEROS hasta el presupuesto de caracteres; nunca
  corta uno (un prompt cortado se calificaría sin ver toda la conversación).
- `ver` pagina un prompt que no cabe solo.
- `responder` recibe JSONL `{"id", "raw"}` y rechaza lo que no se lee.
- `aplicar` recalifica solo las unidades que ya no tienen nada pendiente.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import claude_judge as cli
from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import JudgeQueue


def _prompt(check: str, body: str) -> str:
    return f"Eres auditor.\nCRITERIO {check} — nombre\nCONVERSACIÓN:\n{body}\nDevuelve SOLO un JSON válido:\n"


def _ok(verdict: str = "pasa") -> str:
    return json.dumps({"veredicto": verdict, "turno": None, "evidencia": "", "critica": "ok"})


def _queue(tmp_path: Path) -> tuple[JudgeQueue, dict[str, str]]:
    q = JudgeQueue(tmp_path)
    ids = {
        "a1": q.add_pending(_prompt("CON-04", "a" * 300), unit="wa_573001234567/ep_001"),
        "a2": q.add_pending(_prompt("EST-07", "b" * 300), unit="wa_573001234567/ep_001"),
        "b1": q.add_pending(_prompt("CON-04", "c" * 300), unit="wa_573007654321/ep_002"),
    }
    return q, ids


def test_next_page_brings_whole_prompts_until_the_budget_and_never_cuts_one(tmp_path: Path) -> None:
    q, ids = _queue(tmp_path)
    one = len(q.pending()[0]["prompt"])

    text = cli.page(q, max_chars=one * 2 + 400)

    assert ids["a1"] in text and ids["a2"] in text and ids["b1"] not in text
    assert "a" * 300 in text and "b" * 300 in text


def test_next_page_can_be_limited_to_one_unit(tmp_path: Path) -> None:
    q, ids = _queue(tmp_path)

    text = cli.page(q, max_chars=100_000, unit="wa_573007654321/ep_002")

    assert ids["b1"] in text and ids["a1"] not in text


def test_a_prompt_bigger_than_the_budget_is_not_cut_and_says_how_to_read_it(tmp_path: Path) -> None:
    q = JudgeQueue(tmp_path)
    pid = q.add_pending(_prompt("DES-06", "x" * 5000), unit="wa_573001234567/ep_001")

    text = cli.page(q, max_chars=1000)

    assert "x" * 5000 not in text
    assert f"ver {pid}" in text
    assert cli.show(q, pid, start=0, length=100_000).count("x") == 5000
    assert len(cli.show(q, pid, start=100, length=50)) == 50


def test_respond_keeps_valid_answers_and_reports_the_rest(tmp_path: Path) -> None:
    q, ids = _queue(tmp_path)
    lines = [
        json.dumps({"id": ids["a1"], "raw": _ok("falla")}),
        "",
        "esto no es json",
        json.dumps({"id": ids["a2"], "raw": "no sé"}),
    ]

    saved, errors = cli.respond(q, lines)

    assert saved == 1
    assert len(errors) == 2
    assert q.answer(ids["a1"]) == _ok("falla")


def test_only_units_with_nothing_pending_are_ready_to_apply(tmp_path: Path) -> None:
    q, ids = _queue(tmp_path)
    cli.respond(q, [json.dumps({"id": ids["a1"], "raw": _ok()}), json.dumps({"id": ids["b1"], "raw": _ok()})])

    assert cli.ready_units(q) == ["wa_573007654321/ep_002"]

    cli.respond(q, [json.dumps({"id": ids["a2"], "raw": _ok()})])
    assert cli.ready_units(q) == ["wa_573001234567/ep_001", "wa_573007654321/ep_002"]


def test_summary_counts_by_check_and_unit(tmp_path: Path) -> None:
    q, ids = _queue(tmp_path)
    cli.respond(q, [json.dumps({"id": ids["b1"], "raw": _ok()})])

    s = cli.summary(q)

    assert (s["pendientes"], s["respondidas"]) == (2, 1)
    assert s["por_check"] == {"CON-04": 1, "EST-07": 1}
    assert s["unidades_pendientes"] == 1
