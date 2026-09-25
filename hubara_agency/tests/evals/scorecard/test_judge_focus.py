"""Checks de juez en modo turno (plan del laboratorio §5.2–5.5).

Una llamada por check y por episodio (no por turno): el juez ve el prefijo
real como contexto y cada respuesta candidata marcada `★ CANDIDATA`, y
devuelve un veredicto por turno candidato.
"""
from __future__ import annotations

import json
from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard import judge_checks as jc
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix
from tests.evals.scorecard.test_judge_checks import FakeJudge

_JUDGE_IDS = [c.id for c in CHECKS if c.kind == "judge"]


def _turns(*items: tuple[int, str], **extra) -> str:
    return json.dumps({"turnos": [{"turno": k, "veredicto": v, "evidencia": "cita", "critica": "porque", **extra}
                                  for k, v in items]})


def _candidates():
    real = pr281_before_fix()
    cand3 = replace(real.turns[2], sent_texts=("Tenemos velas de café: ¿te muestro el catálogo?",))
    return real, {3: cand3, 6: real.turns[5]}


async def test_one_call_per_check_per_episode_not_per_turn() -> None:
    real, cands = _candidates()
    judge = FakeJudge(default=_turns((3, "pasa"), (6, "pasa")))

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, samples=2, only=["EST-07"])

    assert len(judge.prompts) == 2
    assert set(out) == {3, 6}
    assert [(r.check_id, r.verdict, r.turn, r.source) for r in out[3]] == [("EST-07", "pasa", 3, "judge")]


async def test_transcript_marks_candidates_and_replaces_the_real_turn() -> None:
    real, cands = _candidates()
    judge = FakeJudge(default=_turns((3, "pasa"), (6, "pasa")))

    await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, samples=1, only=["EST-07"])

    prompt = judge.prompts[0]
    assert "T3 ★ CANDIDATA (a juzgar)" in prompt
    assert "T6 ★ CANDIDATA (a juzgar)" in prompt
    assert "te muestro el catálogo" in prompt
    # La respuesta real de T3 es contexto de T6: va DESPUÉS de su candidata (no la ancla).
    assert prompt.index("T3 ★ CANDIDATA") < prompt.index("¿Para qué espacio sería?")
    assert "T2 · bot envió" in prompt, "el prefijo real es contexto"
    assert "T7 ·" not in prompt, "sin turnos posteriores a la última candidata"
    assert '"turnos"' in prompt


async def test_each_turn_gets_its_own_verdict_and_missing_turns_are_unknown() -> None:
    real, cands = _candidates()
    judge = FakeJudge({"EST-07": [_turns((3, "falla")), _turns((3, "falla"))]})

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, only=["EST-07"])

    assert (out[3][0].verdict, out[3][0].turn, out[3][0].critique) == ("falla", 3, "porque")
    assert out[6][0].verdict == "desconocido"
    assert "no respondió" in out[6][0].critique


async def test_samples_must_agree_per_turn() -> None:
    real, cands = _candidates()
    judge = FakeJudge({"EST-07": [_turns((3, "pasa"), (6, "falla")), _turns((3, "pasa"), (6, "pasa"))]})

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, only=["EST-07"])

    assert out[3][0].verdict == "pasa"
    assert out[6][0].verdict == "desconocido"
    assert "inconsistente" in out[6][0].critique


async def test_applicability_is_decided_per_candidate_on_its_focus_trajectory() -> None:
    real, _ = pr281_before_fix(), None
    judge = FakeJudge(default=_turns((1, "pasa"), (6, "pasa")))

    out = await jc.run_judge_checks_focus(
        real, {1: real.turns[0], 6: real.turns[5]}, CATALOG_CTX, judge, samples=1, only=["EST-07"]
    )

    assert out[1][0].verdict == "no_aplica", "EST-07 exige más de un turno: el foco T1 no tiene prefijo"
    assert out[6][0].verdict == "pasa"
    assert "T1 ★ CANDIDATA" not in judge.prompts[0]


async def test_tag07_without_the_tag_in_the_candidate_is_no_signal() -> None:
    real, cands = _candidates()
    judge = FakeJudge()

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, only=["TAG-07"])

    assert {r.verdict for rs in out.values() for r in rs} == {"sin_senal"}
    assert judge.prompts == []


async def test_est08_topics_are_kept_for_the_candidate_turn() -> None:
    real, cands = _candidates()
    topics = [{"asunto": "precio", "turno": 3, "mensaje": 1, "cubierto": False, "evidencia": ""},
              {"asunto": "viejo", "turno": 2, "mensaje": 1, "cubierto": False, "evidencia": ""}]
    judge = FakeJudge(default=json.dumps({"turnos": [
        {"turno": 3, "veredicto": "pasa", "evidencia": "", "critica": "", "asuntos": topics},
        {"turno": 6, "veredicto": "pasa", "evidencia": "", "critica": "", "asuntos": []},
    ]}))

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, only=["EST-08"])

    r3 = out[3][0]
    assert (r3.verdict, r3.turn) == ("falla", 3)
    assert [t["topic"] for t in r3.topics] == ["precio"]
    # T6 sin asuntos planteados: no hay nada que cubrir (igual que en producción)
    assert out[6][0].verdict == "no_aplica"


async def test_judge_error_is_unknown_for_every_candidate() -> None:
    class Down:
        async def a_generate(self, prompt: str) -> str:
            raise RuntimeError("caído")

    real, cands = _candidates()

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, Down(), only=["EST-07"])

    assert all(jc.is_judge_error(rs[0]) for rs in out.values())


async def test_all_judge_checks_by_default_in_registry_order() -> None:
    real, cands = _candidates()
    judge = FakeJudge(default=_turns((3, "pasa"), (6, "pasa")))

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, samples=1)

    assert [r.check_id for r in out[3]] == _JUDGE_IDS
    assert {r.turn for rs in out.values() for r in rs} == {3, 6}


async def test_no_candidates_no_calls() -> None:
    judge = FakeJudge()

    assert await jc.run_judge_checks_focus(pr281_before_fix(), {}, CATALOG_CTX, judge) == {}
    assert judge.prompts == []


def test_episode_transcript_is_unchanged_without_candidates() -> None:
    real = pr281_before_fix()

    assert jc.render_transcript(real, candidates=None) == jc.render_transcript(real)


async def test_a_future_check_is_judged_one_candidate_at_a_time_without_later_turns(monkeypatch) -> None:
    """Revisión #358: con varias candidatas el transcript llega hasta la última,
    así que el juez de T3 veía T4–T6. Para un check `future` (TAG-07) eso es
    información del futuro que puede dar un `pasa` falso: se pregunta una
    candidata por vez, con el transcript cortado en ella."""
    real, cands = _candidates()
    monkeypatch.setitem(jc._APPLIES, "TAG-07", lambda traj, ctx: (True, ""))
    judge = FakeJudge(default=_turns((3, "pasa"), (6, "pasa")))

    out = await jc.run_judge_checks_focus(real, cands, CATALOG_CTX, judge, samples=1, only=["TAG-07"])

    assert len(judge.prompts) == 2
    first = next(p for p in judge.prompts if "T3 ★ CANDIDATA" in p)
    assert "T4 ·" not in first and "T6 ★" not in first
    assert (out[3][0].verdict, out[6][0].verdict) == ("pasa", "pasa")
