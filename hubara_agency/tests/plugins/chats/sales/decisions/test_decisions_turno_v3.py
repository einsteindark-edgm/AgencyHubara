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

import pytest

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
    """Sin la pregunta del costo (cuestionarios hasta v4) la regla es la de
    siempre: «envío» pide la tarjeta de tarifas."""
    turn = POLICY.decide_turn(_result(_noul("topic.envio", 0.94)), questionnaire=RAFAGA,
                              context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH)

    [required] = turn.tools["required"]
    assert required["topic"] == "envio" and required["any_of"] == ["send_shipping_rates"]
    assert "send_shipping_rates" in required["nudge"]


def test_a_question_about_delivery_time_does_not_need_the_rates_card() -> None:
    """Laboratorio caso-fotos-0929-r5 (6543, turno 6): «¿cuánto se demora el
    envío a Medellín?» dio «envío» y «tiempos». El contrato exigía la tarjeta de
    tarifas (que solo trae costos y termina el turno) y la respuesta del tiempo
    quedó retenida: salió solo la tarjeta. Con `rafaga-v5` Jev dice si pregunta
    el costo (`envio.costo`, 0,03 en ese mensaje con Jev real)."""
    turn = POLICY.decide_turn(
        _result(_noul("topic.envio", 0.78), _noul("topic.tiempos", 0.97), _noul("envio.costo", 0.03)),
        questionnaire=RAFAGA, context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH,
    )

    assert not [r for r in turn.tools.get("required") or [] if r["topic"] == "envio"]


def test_a_question_about_the_shipping_cost_needs_the_card_and_the_rest_in_the_same_step() -> None:
    """Con Jev real: «¿cuánto vale el envío?» 0,99; «¿el envío es gratis?»
    0,76–0,80 (pasa el umbral de detección). La tarjeta termina el turno: lo
    demás que preguntó (el tiempo, por ejemplo) va con `send_reply` en el
    mismo paso, o se pierde."""
    for p in (0.99, 0.78):
        turn = POLICY.decide_turn(
            _result(_noul("topic.envio", 0.98), _noul("topic.tiempos", 0.96), _noul("envio.costo", p)),
            questionnaire=RAFAGA, context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH,
        )

        [required] = [r for r in turn.tools["required"] if r["topic"] == "envio"]
        assert required["any_of"] == ["send_shipping_rates"]
        assert "send_reply en el mismo paso" in required["nudge"]


def test_sizes_are_answered_by_the_search_too() -> None:
    """`search_products` ya trae las medidas de cada producto (`medidas`): el
    contrato no puede exigir otra consulta para lo mismo. Con la segunda puerta
    también en `send_reply` (caso 6543, 2026-09-29), exigirla forzaba una
    ronda de más en los turnos que ya habían buscado (4567 t4, 4329 t3 en r4)."""
    turn = POLICY.decide_turn(_result(_noul("topic.medidas", 0.93)), questionnaire=RAFAGA,
                              context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH)

    [required] = turn.tools["required"]
    assert required["topic"] == "medidas"
    assert {"search_products", "get_product_by_handle", "present_product_detail"} <= set(required["any_of"])


def test_a_variant_is_answered_with_the_picker_in_any_stage() -> None:
    """Laboratorio caso-fotos-0930-r10, 4567 t20: «mejor la de los pajaritos /
    en lila». La etapa al empezar el turno era descubrimiento, así que el
    contrato pedía la ficha: el bot anotó producto y color, le escribió los
    aromas como texto, el contrato lo retuvo por la ficha y el modelo mostró la
    tarjeta en el diseño equivocado (azul) antes de volver a escribir la lista
    (la protección la cambió por el selector). El selector trae las opciones
    del catálogo: cumple lo que pedía la ficha, y es lo que hay que mandar
    para que escoja."""
    for stage in ("etapa_descubrimiento", "etapa_variantes"):
        turn = POLICY.decide_turn(_result(_noul("topic.variante", 0.91)), questionnaire=RAFAGA,
                                  context=_ctx(stage), n_messages=1, thresholds=TH)

        [required] = [r for r in turn.tools["required"] if r["topic"] == "variante"]
        assert "present_variant_picker" in required["any_of"], stage
        assert "present_variant_picker" in required["nudge"], stage


#: Laboratorio caso-fotos-0930, 4567 t22 (r10 y r11): el asesor le preguntó
#: cuántas quería y el cliente contestó todo junto («Mejor 2, una lila y otra
#: azul. Me las mandas a Chía…, cuánto vale el envío?»). Jev leyó bien que
#: respondía la pregunta de variantes, pero también leyó «pregunta por colores»
#: (0,73): el contrato pidió el selector, la guía dijo «pide solo lo que falta:
#: cantidad» y la revisión final mandó un complemento «Sobre los colores…» que
#: nadie había preguntado. Producción solo guardó los datos y mandó las tarifas.
ANSWERS_THE_VARIANT_QUESTION = (
    TypedAnswer(id="thread.bot_asked", kind="choice", choice="elegir_variante",
                probs=(("elegir_variante", 0.97), ("pregunta_abierta", 0.03)), confidence=0.97),
    TypedAnswer(id="thread.answers_bot", kind="noul", p=0.82),
)


def test_colors_the_customer_picks_when_answering_are_a_choice_not_a_question() -> None:
    turn = POLICY.decide_turn(
        _result(*ANSWERS_THE_VARIANT_QUESTION, _noul("topic.variante", 0.73), _noul("topic.envio", 0.98),
                _noul("envio.costo", 0.99)),
        questionnaire=RAFAGA, context=_ctx("etapa_variantes", missing=("cantidad",)), n_messages=1, thresholds=TH,
    )

    assert [t["topic"] for t in turn.topics] == ["envio"]
    assert "variante" not in turn.coverage
    assert [r["topic"] for r in turn.tools["required"]] == ["envio"]
    assert turn.guide.get("chosen_now") == ["variante"]
    assert "colores o variantes" not in turn.note and "pide solo lo que falta" not in turn.note
    assert "guarda con set_order_slot lo que eligió" in turn.note


def test_a_clear_question_about_colors_still_needs_the_picker() -> None:
    """«Lila. ¿Y la tienen en rojo?»: responde y además pregunta."""
    turn = POLICY.decide_turn(
        _result(*ANSWERS_THE_VARIANT_QUESTION, _noul("topic.variante", 0.95)),
        questionnaire=RAFAGA, context=_ctx("etapa_variantes", missing=("cantidad",)), n_messages=1, thresholds=TH,
    )

    [required] = [r for r in turn.tools["required"] if r["topic"] == "variante"]
    assert "present_variant_picker" in required["any_of"]
    assert "colores o variantes" in turn.note and turn.guide.get("chosen_now") == []


def test_jev_saying_the_customer_chooses_is_a_choice_too() -> None:
    turn = POLICY.decide_turn(
        _result(_noul("variantes.elige", 0.93), _noul("topic.variante", 0.74), _noul("topic.aroma", 0.71)),
        questionnaire=RAFAGA, context=_ctx("etapa_variantes", missing=("cantidad",)), n_messages=1, thresholds=TH,
    )

    assert turn.topics == [] and turn.tools["required"] == []
    assert turn.guide.get("chosen_now") == ["variante", "aroma"]


def test_a_customer_who_is_choosing_is_not_stuck() -> None:
    """Tres turnos sin un dato nuevo cuentan hasta ANTES de este mensaje: si
    ahora elige, «pide de forma concreta la cantidad» contradice la guía."""
    turn = POLICY.decide_turn(
        _result(*ANSWERS_THE_VARIANT_QUESTION, _noul("topic.variante", 0.73)),
        questionnaire=RAFAGA, context=_ctx("etapa_variantes", missing=("cantidad",), stagnant=3), n_messages=1,
        thresholds=TH,
    )

    assert "Llevan 3 turnos" not in turn.note


@pytest.mark.parametrize("stage", ["etapa_descubrimiento", "etapa_postcierre"])
def test_a_customer_who_only_thanks_is_not_pushed_to_buy(stage: str) -> None:
    """Caso de producción del 2026-09-29: el cliente contestó el «pedido
    listo» del ETA con un agradecimiento; el episodio nuevo cae en
    descubrimiento y la guía decía «ayúdale a escoger un producto». Con la
    lectura `cortesia` (sin venta en curso), respuesta breve y cálida."""
    ctx = TurnContext(window=Window(lines=("[asesor] Hola, tu pedido #47 ya está listo.",)), facts=(f"Etapa: {stage}",),
                      stage=stage, courtesy=True)

    turn = POLICY.decide_turn(_result(_noul("topic.saludo", 0.9)), questionnaire=RAFAGA, context=ctx, n_messages=1,
                              thresholds=TH)

    assert "ayúdale a escoger" not in turn.note and "solo agradece" in turn.note
    assert turn.guide.get("courtesy") is True


def test_a_thank_you_in_the_middle_of_a_sale_keeps_the_next_step() -> None:
    ctx = TurnContext(window=Window(lines=("[asesor] ¿Qué color quieres?",)), facts=("Etapa: etapa_variantes",),
                      stage="etapa_variantes", missing=("color",), courtesy=True)

    turn = POLICY.decide_turn(_result(), questionnaire=RAFAGA, context=ctx, n_messages=1, thresholds=TH)

    assert "pide solo lo que falta: color" in turn.note and "solo agradece" not in turn.note


def test_before_choosing_the_product_the_card_also_answers_a_variant() -> None:
    turn = POLICY.decide_turn(_result(_noul("topic.variante", 0.91)), questionnaire=RAFAGA,
                              context=_ctx("etapa_descubrimiento"), n_messages=1, thresholds=TH)

    [required] = [r for r in turn.tools["required"] if r["topic"] == "variante"]
    assert {"present_product_detail", "get_product_by_handle"} <= set(required["any_of"])


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
