"""EST-08 v2 — el juez ve la ráfaga mensaje por mensaje y mide cada asunto.

Caso que motivó el cambio (plan del laboratorio §2): el cliente escribe
"¿me mandas el catálogo?" y 7 s después "y el envío a Bogotá cuánto sale". El
bot manda las tarifas de envío y el turno termina: el catálogo queda sin
respuesta. EST-08 v1 no lo veía por tres razones:

  * el prefiltro exigía un "?" (una ráfaga de pedidos no lo trae);
  * `render_transcript` juntaba los mensajes en una línea y cortaba a 400;
  * el juez devolvía un veredicto sin decir QUÉ asunto quedó sin respuesta.
"""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales_eval.scorecard import judge_checks as jc
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.service import score_trajectory
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import build_trajectory
from tests.evals.scorecard.dsl import T, tool, traj
from tests.evals.scorecard.test_judge_checks import FakeJudge


def _burst_catalog_and_shipping():
    """Ráfaga de dos pedidos sin "?"; el bot solo atiende el envío."""
    return traj(
        T(1, inbound="hola", sent=["¡Buenas tardes! Bienvenido a *Hubara*"]),
        T(
            2,
            inbound="me mandas el catálogo porfa\ny el envío a Bogotá cuánto sale",
            tools=[tool("send_shipping_rates")],
        ),
    )


def _answer(verdict: str, topics: list[dict], turn=None) -> str:
    return json.dumps(
        {"veredicto": verdict, "turno": turn, "evidencia": "", "critica": "", "asuntos": topics}
    )


_TOPICS_ONE_MISSED = [
    {"asunto": "catálogo", "turno": 2, "mensaje": 1, "cubierto": False, "evidencia": "me mandas el catálogo"},
    {"asunto": "envío", "turno": 2, "mensaje": 2, "cubierto": True, "evidencia": "tarifas de envío"},
]


async def test_burst_without_question_mark_is_judged_and_fails_on_the_missed_topic() -> None:
    judge = FakeJudge({"EST-08": [_answer("falla", _TOPICS_ONE_MISSED, 2)] * 2})

    [result] = await jc.run_judge_checks(_burst_catalog_and_shipping(), CheckContext(), judge, only={"EST-08"})

    assert result.verdict == "falla"
    assert result.turn == 2
    assert any("CRITERIO EST-08" in p for p in judge.prompts)


async def test_customer_who_only_writes_nothing_is_not_applicable() -> None:
    judge = FakeJudge()
    t = traj(T(1, trigger="ghost", inbound="", sent=[]))

    [result] = await jc.run_judge_checks(t, CheckContext(), judge, only={"EST-08"})

    assert result.verdict == "no_aplica"
    assert judge.prompts == []


def test_transcript_lists_each_message_of_the_burst_on_its_own_line() -> None:
    text = jc.render_transcript(_burst_catalog_and_shipping())

    lines = text.splitlines()
    assert "T2 · cliente escribió 2 mensajes:" in lines
    assert "T2 ·   [1] me mandas el catálogo porfa" in lines
    assert "T2 ·   [2] y el envío a Bogotá cuánto sale" in lines


def test_transcript_does_not_clip_long_messages() -> None:
    long_msg = "quiero una vela para mi mamá " * 20  # ~580 caracteres
    text = jc.render_transcript(traj(T(1, inbound=long_msg.strip(), sent=["Claro"])))

    assert long_msg.strip() in text


def test_bot_text_keeps_its_line_breaks() -> None:
    text = jc.render_transcript(traj(T(1, inbound="precios", sent=["Tenemos:\n• Cubo Love\n• Duo Zodiacal"])))

    assert 'T1 · bot envió: "Tenemos:' in text
    assert "T1 ·   • Cubo Love" in text.splitlines()


def test_trajectory_reads_the_burst_messages_with_their_times_when_the_trace_has_them() -> None:
    raw = {
        "turn": 3,
        "trigger": "customer",
        "inbound_text": "me mandas el catálogo\ny el envío",
        "inbound": [
            {"seq": 41, "wamid": "wamid.A", "ts_ms": 1_000, "kind": "text", "text": "me mandas el catálogo"},
            {"seq": 42, "wamid": "wamid.B", "ts_ms": 8_000, "kind": "text", "text": "y el envío"},
        ],
    }

    t = build_trajectory([raw], session_id="wa_573001234567", episode={"episode_id": "ep_001"})

    [turn] = t.turns
    assert [m.text for m in turn.inbound] == ["me mandas el catálogo", "y el envío"]
    lines = jc.render_transcript(t).splitlines()
    assert "T3 ·   [1] me mandas el catálogo" in lines
    assert "T3 ·   [2] (+7 s) y el envío" in lines


def test_only_est08_asks_the_judge_for_topics() -> None:
    t = _burst_catalog_and_shipping()

    assert '"asuntos"' in jc.build_prompt("EST-08", t, CheckContext())
    assert '"asuntos"' not in jc.build_prompt("EST-04", t, CheckContext())


def test_judge_topics_are_parsed_into_the_result() -> None:
    result = jc.parse_judge_output("EST-08", _answer("falla", _TOPICS_ONE_MISSED, 2))

    assert result is not None
    assert result.topics == (
        {"topic": "catálogo", "turn": 2, "msg": 1, "covered": False, "evidence": "me mandas el catálogo"},
        {"topic": "envío", "turn": 2, "msg": 2, "covered": True, "evidence": "tarifas de envío"},
    )


def test_a_missed_topic_wins_over_a_lenient_verdict() -> None:
    """El veredicto sale de los asuntos: si el juez dice `pasa` pero marca un
    asunto sin cubrir, es `falla` en el turno de ese asunto."""
    result = jc.parse_judge_output("EST-08", _answer("pasa", _TOPICS_ONE_MISSED))

    assert result is not None
    assert (result.verdict, result.turn) == ("falla", 2)
    assert "catálogo" in result.evidence


def test_all_topics_covered_passes() -> None:
    topics = [dict(t, cubierto=True) for t in _TOPICS_ONE_MISSED]

    result = jc.parse_judge_output("EST-08", _answer("pasa", topics))

    assert result is not None and result.verdict == "pasa"


def test_a_failure_that_names_no_uncovered_topic_is_unknown() -> None:
    """El juez escribe `falla` pero todos los asuntos que lista están cubiertos:
    sin el asunto que faltó, la falla no se puede sostener (antes se volvía
    `pasa` con solo omitir el asunto no cubierto)."""
    topics = [dict(t, cubierto=True) for t in _TOPICS_ONE_MISSED]

    result = jc.parse_judge_output("EST-08", _answer("falla", topics))

    assert result is not None and result.verdict == "desconocido"


def test_no_topics_means_nothing_to_cover() -> None:
    """Un "hola" no plantea asuntos: EST-08 no aplica. Antes el veredicto del
    juez se respetaba y un `pasa` gratis subía el cumplimiento sin que el bot
    hiciera nada."""
    lenient = jc.parse_judge_output("EST-08", _answer("pasa", []))
    strict = jc.parse_judge_output("EST-08", _answer("falla", []))

    assert lenient is not None and lenient.verdict == "no_aplica"
    assert strict is not None and strict.verdict == "desconocido"


def test_topics_travel_to_the_scorecard_row() -> None:
    judged = jc.parse_judge_output("EST-08", _answer("falla", _TOPICS_ONE_MISSED, 2))
    assert isinstance(judged, CheckResult)

    record = score_trajectory(_burst_catalog_and_shipping(), CheckContext(), judge_results=[judged])

    row = next(r for r in record["results"] if r["check_id"] == "EST-08")
    assert [t["topic"] for t in row["topics"]] == ["catálogo", "envío"]
    assert record["registry_version"] == 3


def test_rows_without_topics_keep_the_v2_shape() -> None:
    record = score_trajectory(_burst_catalog_and_shipping(), CheckContext())

    assert all("topics" not in r for r in record["results"])


def test_registry_describes_the_burst_rule() -> None:
    spec = SPECS_BY_ID["EST-08"]

    assert REGISTRY_VERSION == 3
    assert "ráfaga" in spec.rule.lower() or "ráfaga" in spec.applies.lower()
