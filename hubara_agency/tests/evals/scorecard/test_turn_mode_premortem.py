"""Premortem del scorecard en modo turno (PR 12) y de su lectura de producción.

* El contexto de cada candidata es la conversación REAL anterior a ella (con
  todas las candidatas en un prompt, antes el juez veía T1★…Tn★ sin ningún
  turno real del bot: el cliente de Tk+1 reaccionaba a una respuesta que no
  estaba). La respuesta real de un turno aparece DESPUÉS de su candidata: es
  contexto de los turnos siguientes, no ancla para juzgarla.
* En modo turno, un asunto que la candidata deja pendiente con un compromiso
  ("ya te paso el catálogo") no es falla: la regla lo admite para el turno
  siguiente. En producción (el juez ve el turno siguiente) sí se exige.
* El complemento del bot nuevo (turno aparte con `trigger: complement`) es
  parte del turno que complementa: el scorecard de producción lo une a ese
  turno y el juez no ve la nota `[SISTEMA]` con los asuntos del clasificador.
* Un check agregado sobre varios turnos: `falla` > `desconocido` > `pasa`
  (un turno sin decidir no se esconde detrás de otro que pasó).
* La candidata del turno k se juzga como turno k aunque venga numerada distinto.
"""
from __future__ import annotations

import json
from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard import judge_checks as jc
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.service import aggregate_checks, score_trajectory, score_turns
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import build_trajectory
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix

SID = "wa_573001234567"


def _candidates():
    real = pr281_before_fix()
    cand3 = replace(real.turns[2], sent_texts=("Tenemos velas de café: ¿te muestro el catálogo?",))
    return real, {3: cand3, 6: real.turns[5]}


def test_each_candidate_is_judged_after_the_real_conversation_that_preceded_it() -> None:
    real, cands = _candidates()
    real_t3 = real.turns[2].sent_texts[0]

    prompt = jc.build_focus_prompt("EST-07", real, cands, CATALOG_CTX)

    customer_t3 = prompt.index("T3 · cliente")
    candidate_t3 = prompt.index("T3 ★ CANDIDATA")
    assert customer_t3 < candidate_t3 < prompt.index(real_t3), "la respuesta real de T3 va DESPUÉS de su candidata"
    assert "T2 · bot envió" in prompt, "el prefijo real es contexto"
    # T6 es igual al turno real (A0): una sola vez, marcada.
    assert prompt.count("T6 ★ CANDIDATA (a juzgar)") == 1
    assert "T7 ·" not in prompt, "sin turnos posteriores a la última candidata"


def test_every_candidate_of_a_simulated_arm_sits_on_the_real_prefix() -> None:
    """A1/B/C simulan TODOS los turnos: antes el transcript quedaba T1★…Tn★,
    sin una sola respuesta real del bot."""
    real = pr281_before_fix()
    cands = {t.turn: replace(t, sent_texts=(f"alternativa {t.turn}",)) for t in real.turns[:3]}

    prompt = jc.build_focus_prompt("EST-07", real, cands, CATALOG_CTX)

    for t in real.turns[:2]:  # las respuestas reales de T1 y T2 son contexto de T2★ y T3★
        for text in t.sent_texts:
            assert text[:40] in prompt
    assert real.turns[2].sent_texts[0][:40] not in prompt, "la respuesta real de la última candidata no hace falta"


def test_a_topic_left_pending_with_a_commitment_is_not_a_failure_in_turn_mode() -> None:
    item = {"turno": 3, "veredicto": "pasa", "evidencia": "", "critica": "", "asuntos": [
        {"asunto": "catálogo", "turno": 3, "mensaje": 1, "cubierto": False, "pendiente": True, "evidencia": "ya te lo paso"},
        {"asunto": "envío", "turno": 3, "mensaje": 2, "cubierto": True, "evidencia": "cuesta X"},
    ]}
    pending = jc.parse_focus_output("EST-08", json.dumps({"turnos": [item]}), [3])
    forgotten = jc.parse_focus_output(
        "EST-08", json.dumps({"turnos": [{**item, "asuntos": [dict(item["asuntos"][0], pendiente=False), item["asuntos"][1]]}]}), [3]
    )

    assert pending[3].verdict == "pasa"
    assert forgotten[3].verdict == "falla"
    assert '"pendiente"' in jc.build_focus_prompt("EST-08", *_candidates(), CATALOG_CTX)


def test_production_still_requires_the_topic_to_be_covered() -> None:
    """El juez de producción ve el turno siguiente: lo pendiente se juzga ahí."""
    answer = {"veredicto": "pasa", "turno": 3, "evidencia": "", "critica": "", "asuntos": [
        {"asunto": "catálogo", "turno": 3, "mensaje": 1, "cubierto": False, "pendiente": True, "evidencia": ""},
    ]}

    result = jc.parse_judge_output("EST-08", json.dumps(answer))

    assert result is not None and result.verdict == "falla"


def _trace(turn: int, trigger: str, inbound: str, sent: list[str], tools: list[dict] | None = None) -> dict:
    return {
        "v": 2, "session_id": SID, "episode_id": "ep_001", "turn": turn, "trigger": trigger,
        "turn_started_ms": 1_790_200_000_000 + turn * 60_000, "recorded_at_ms": 1_790_200_000_000 + turn * 60_000 + 5_000,
        "inbound_text": inbound, "sent_texts": sent, "tools": tools or [], "intents": [], "guards": [],
        "state": {}, "mode": "on",
    }


def test_the_complement_is_part_of_the_turn_it_completes() -> None:
    traces = [
        _trace(1, "customer", "hola, ¿me mandas el catálogo? y ¿cuánto sale el envío a Bogotá?", ["¡Hola! Te dejo el catálogo 👇"],
               [{"name": "send_catalog", "ok": True, "args": {}}]),
        _trace(2, "complement", "[SISTEMA] Complemento del turno: el cliente también preguntó por envio", ["El envío a Bogotá cuesta $12.900"]),
        _trace(3, "customer", "perfecto", ["¡Genial!"]),
    ]

    traj = build_trajectory(traces, session_id=SID, episode={"episode_id": "ep_001"})

    assert [t.turn for t in traj.turns] == [1, 3]
    assert traj.turns[0].sent_texts == ("¡Hola! Te dejo el catálogo 👇", "El envío a Bogotá cuesta $12.900")
    transcript = jc.render_transcript(traj)
    assert "[SISTEMA]" not in transcript and "complement" not in transcript


def test_a_check_undecided_in_one_turn_is_not_hidden_behind_a_pass() -> None:
    rows = [
        {"check_id": "EST-07", "verdict": "pasa"},
        {"check_id": "EST-07", "verdict": "desconocido"},
        {"check_id": "EST-06", "verdict": "desconocido"},
        {"check_id": "EST-06", "verdict": "falla"},
        {"check_id": "APE-01", "verdict": "pasa"},
        {"check_id": "APE-01", "verdict": "no_aplica"},
    ]

    assert aggregate_checks(rows) == {"EST-07": "desconocido", "EST-06": "falla", "APE-01": "pasa"}


def test_the_candidate_of_turn_k_is_judged_as_turn_k() -> None:
    real = pr281_before_fix()
    wrong_number = replace(real.turns[1], turn=99)

    record = score_turns(real, {2: wrong_number}, CheckContext())

    [only] = record["by_turn"]
    assert only["turn"] == 2
    assert all(r.get("turn") in (None, 2) for r in only["results"])


def test_the_record_counts_the_answers_the_judge_could_not_decide() -> None:
    """Una respuesta ilegible o inconsistente no es un error de conexión pero
    tampoco un juicio: el registro la cuenta aparte."""
    judged = [
        CheckResult("EST-07", "desconocido", critique="respuesta del juez ilegible", source="judge"),
        CheckResult("EST-04", "desconocido", critique=f"{jc.JUDGE_ERROR_PREFIX}: timeout", source="judge"),
        CheckResult("EST-06", "pasa", source="judge"),
    ]

    record = score_trajectory(pr281_before_fix(), CheckContext(), judge_results=judged)

    assert (record["judge_errors"], record["judge_unknown"]) == (1, 1)
