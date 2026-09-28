"""Perfil `jev-v3`: cuestionario `rafaga-v3` + política `turno-v3` (diseño v2
§04 y §05, fase F6).

* Contrato de herramientas: Jev dice qué pide el cliente; el código dice qué
  tool lo resuelve. Las tools requeridas viajan GRABADAS en el resultado
  (`TurnDecisions.tools.required`), con su nota, para la segunda puerta del
  turno y la auditoría antes de enviar.
* Etapas: la etapa la sigue calculando el código (borrador). Jev aporta
  evidencia (qué datos dio en este turno, si quiere devolverse, si confirma el
  resumen) y el motor le da al LLM el siguiente paso concreto. Las preguntas
  son condicionales: solo las de la etapa actual.
* Estancamiento: tres turnos en la misma etapa sin un dato nuevo refuerzan el
  siguiente paso en la nota y quedan en la traza.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window, stagnant_turns
from src.plugins.chats.agent.sales.decisions.policies import get_policy
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

RAFAGA = load_questionnaire("rafaga-v3")
POLICY = get_policy("turno-v3")
TH = {"detect": 0.70, "confidence": 0.60, "covered": 0.70, "answers": 0.70, "purchase_confirm": 0.85,
      "purchase_retract": 0.20, "given": 0.85}
BURST = [{"text": "Soy de Medellín", "ts_ms": 1_000}]


def _ctx(stage: str, *, missing: tuple[str, ...] = (), stagnant: int = 0) -> TurnContext:
    return TurnContext(window=Window(lines=("[asesor] ¿A qué ciudad te lo enviamos?",)), facts=(f"Etapa: {stage}",),
                       stage=stage, missing=missing, stagnant=stagnant)


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def _ids(stage: str) -> list[str]:
    return [q.id for q in RAFAGA.burst_questions(BURST, facts=_ctx(stage).when_facts())]


def test_only_the_questions_of_the_current_stage_are_asked() -> None:
    datos = _ids("etapa_datos_envio")
    descubrimiento = _ids("etapa_descubrimiento")

    assert {"datos.ciudad", "datos.direccion", "datos.telefono", "datos.nombre_recibe", "datos.metodo_pago"} <= set(datos)
    assert not [i for i in descubrimiento if i.startswith("datos.")]
    assert "etapa.cambia_producto" in datos and "etapa.cambia_producto" not in descubrimiento
    assert "cierre.confirma_resumen" in _ids("etapa_cierre") and "cierre.confirma_resumen" not in datos


def test_shipping_needs_the_rates_tool() -> None:
    turn = POLICY.decide_turn(_result(_noul("topic.envio", 0.94)), questionnaire=RAFAGA,
                              context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH)

    [required] = turn.tools["required"]
    assert required["topic"] == "envio" and required["any_of"] == ["send_shipping_rates"]
    assert "send_shipping_rates" in required["nudge"]


def test_a_price_already_on_screen_needs_no_tool() -> None:
    seen = POLICY.decide_turn(_result(_noul("topic.precio", 0.9), _noul("precio.en_contexto", 0.95)), questionnaire=RAFAGA,
                              context=_ctx("etapa_variantes"), n_messages=1, thresholds=TH)
    unseen = POLICY.decide_turn(_result(_noul("topic.precio", 0.9), _noul("precio.en_contexto", 0.05)), questionnaire=RAFAGA,
                                context=_ctx("etapa_variantes"), n_messages=1, thresholds=TH)

    assert seen.tools["required"] == []
    assert [r["topic"] for r in unseen.tools["required"]] == ["precio"]


def test_data_the_customer_gave_must_be_saved() -> None:
    turn = POLICY.decide_turn(
        _result(_noul("topic.datos_envio", 0.9), _noul("datos.ciudad", 0.95), _noul("datos.telefono", 0.02)),
        questionnaire=RAFAGA, context=_ctx("etapa_datos_envio", missing=("ciudad", "telefono", "metodo_pago")),
        n_messages=1, thresholds=TH,
    )

    [required] = [r for r in turn.tools["required"] if r["topic"] == "datos_envio"]
    assert required["any_of"] == ["set_order_slot"] and required["fields"] == ["ciudad"]
    assert turn.guide["given_now"] == ["ciudad"] and turn.guide["missing"] == ["telefono", "metodo_pago"]
    assert "[ETAPA] Datos de envío" in turn.note and "ciudad" in turn.note
    assert "Siguiente paso" in turn.note and "teléfono" in turn.note and "método de pago" in turn.note


def test_a_complaint_about_a_placed_order_goes_to_a_human() -> None:
    turn = POLICY.decide_turn(_result(_noul("topic.queja", 0.9), _noul("queja.pedido_hecho", 0.93)), questionnaire=RAFAGA,
                              context=_ctx("etapa_postcierre"), n_messages=1, thresholds=TH)

    assert [r["any_of"] for r in turn.tools["required"]] == [["escalate_to_human"]]


def test_going_back_asks_to_remove_the_previous_product() -> None:
    turn = POLICY.decide_turn(_result(_noul("etapa.cambia_producto", 0.92)), questionnaire=RAFAGA,
                              context=_ctx("etapa_datos_envio"), n_messages=1, thresholds=TH)

    assert "quitar=true" in turn.note and turn.guide["going_back"] is True


def test_three_turns_without_new_data_reinforce_the_next_step() -> None:
    turn = POLICY.decide_turn(_result(), questionnaire=RAFAGA,
                              context=_ctx("etapa_datos_envio", missing=("telefono",), stagnant=3), n_messages=1, thresholds=TH)

    assert turn.guide["stagnant"] == 3
    assert "3 turnos" in turn.note and "teléfono" in turn.note


def test_stagnation_counts_trailing_turns_in_the_same_stage_without_new_data() -> None:
    def trace(stage: str, draft: dict, episode: str = "ep_1") -> dict:
        return {"episode_id": episode, "stage_out": stage, "draft": draft, "trigger": "customer"}

    same = {"producto": "Cubo", "ciudad": "Medellín"}
    traces = [
        trace("variantes", {"producto": "Cubo"}),
        trace("datos_envio", same),
        trace("datos_envio", same),
        trace("datos_envio", same),
    ]

    assert stagnant_turns(traces, episode_id="ep_1") == 3
    assert stagnant_turns([*traces, trace("datos_envio", {**same, "telefono": "x"})], episode_id="ep_1") == 1
    assert stagnant_turns(traces, episode_id="ep_2") == 0
    # Un dato que llegó después del último turno (p. ej. por el ingest) ya no
    # es estancamiento: se compara el borrador de ahora con el de la traza.
    assert stagnant_turns(traces, episode_id="ep_1", draft=same) == 3
    assert stagnant_turns(traces, episode_id="ep_1", draft={**same, "telefono": "x"}) == 0
