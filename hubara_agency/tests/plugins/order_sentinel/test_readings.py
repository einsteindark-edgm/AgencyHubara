"""La lectura de Jev del estado del pedido (motor de decisiones, F8).

Hoy el LLM del Order Sentinel lee la conversación y propone el cambio. Con el
lector `sombra` o `jev`, hubara le hace a Jev dos preguntas cerradas antes de
despachar: «¿qué cambió?» (choice) y, si cambió algo, un sí/no por cada
mensaje que podría probarlo. El código arma el veredicto con la MISMA forma
del LLM y las guardas del grafo quedan iguales para las dos fuentes. Si Jev
duda, cae o tarda, no hay veredicto de Jev: decide el LLM, como hoy.
"""
from __future__ import annotations

from src.plugins.order_sentinel.agent.cycle.use_cases.readings import (
    CHANGE_QID,
    change_request,
    evidence_request,
    reader_mode,
    reading_from,
    redact_terms,
)
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

SID = "wa_573001234567"


def _convo(messages, *, stage="ready", paid=True) -> dict:
    return {
        "session_id": SID,
        "order_id": "order_01SALIO",
        "current_stage": stage,
        "payment_confirmed": paid,
        "messages": [
            {"who": who, "text": text, "at_ms": 1_000 + i, "has_media": media}
            for i, (who, text, media) in enumerate(messages)
        ],
    }


SALIO = _convo(
    [
        ("customer", "hola, ¿mi pedido ya salió?", False),
        ("bot", "Ya le aviso al equipo 🙌", False),
        ("human_operator", "Hola! ya salió con el mensajero,   llega hoy", False),
        ("human_operator", "", True),
        ("customer", "gracias!!", False),
    ]
)


def _choice(choice: str, p: float) -> PerceptionResult:
    return PerceptionResult(
        ok=True,
        model="typesafe/jev-1.13",
        answers=(TypedAnswer(id=CHANGE_QID, kind="choice", choice=choice, probs=((choice, p),), confidence=p),),
    )


def _evidence(**ps: float) -> PerceptionResult:
    return PerceptionResult(
        ok=True,
        model="typesafe/jev-1.13",
        answers=tuple(TypedAnswer(id=f"estado_pedido.evidencia.{i[1:]}", kind="noul", p=p) for i, p in ps.items()),
    )


def test_el_lector_nace_en_reglas_y_solo_sube_con_el_valor_de_terraform():
    assert reader_mode(None) == "reglas"
    assert reader_mode("") == "reglas"
    assert reader_mode("off") == "reglas"
    assert reader_mode("jev") == "reglas", "solo los valores de Terraform: off | shadow | on"
    assert reader_mode(" Shadow ") == "sombra"
    assert reader_mode("on") == "jev"


def test_la_pregunta_del_cambio_ve_la_conversacion_numerada_y_lo_que_el_sistema_ya_sabe():
    state, [question] = change_request(SALIO)

    assert "pedido listo; pago confirmado: sí" in state
    assert "[1] cliente: hola, ¿mi pedido ya salió?" in state
    assert "[2] bot de la tienda: Ya le aviso al equipo 🙌" in state
    assert "[3] equipo de la tienda: Hola! ya salió con el mensajero, llega hoy" in state
    assert "[4] equipo de la tienda: [adjuntó una imagen]" in state
    assert question.id == CHANGE_QID
    assert question.kind == "choice"
    assert question.options == ("nada", "preparacion", "listo", "en_camino", "entregado", "pago")


def test_la_evidencia_se_pregunta_por_mensaje_del_equipo_o_del_cliente_con_texto():
    state, questions = evidence_request(SALIO, "en_camino")

    assert state == change_request(SALIO)[0], "misma conversación, misma numeración"
    assert [q.id for q in questions] == [
        "estado_pedido.evidencia.1",
        "estado_pedido.evidencia.3",
        "estado_pedido.evidencia.5",
    ], "ni el bot ni un mensaje sin texto pueden ser evidencia"
    assert all(q.kind == "noul" for q in questions)
    assert "ya salió o va en camino" in questions[1].text
    assert "ya salió con el mensajero" in questions[1].text


def test_el_pago_solo_lo_prueba_el_equipo():
    convo = _convo([("customer", "ya pagué, te mando el comprobante", False), ("human_operator", "recibido", False)])

    _, questions = evidence_request(convo, "pago")

    assert [q.id for q in questions] == ["estado_pedido.evidencia.2"]


def test_sin_mensajes_candidatos_no_hay_pregunta_de_evidencia():
    convo = _convo([("customer", "ya pagué", False), ("bot", "¡Gracias!", False)])

    assert evidence_request(convo, "pago") is None


def test_la_evidencia_mira_a_lo_sumo_los_ultimos_dieciseis_candidatos():
    convo = _convo([("human_operator", f"mensaje {i}", False) for i in range(20)])

    _, questions = evidence_request(convo, "listo")

    assert [q.id for q in questions] == [f"estado_pedido.evidencia.{i}" for i in range(5, 21)]


def test_la_evidencia_solo_se_busca_en_los_mensajes_nuevos_desde_el_ultimo_analisis():
    """Caso real (ciclo del 2026-09-18 UTC, el único despacho en 30 días): la prueba
    era un mensaje NUEVO del equipo, pero detrás de otros 11 candidatos. Lo
    que cambió desde el último análisis tiene que estar en lo nuevo; lo viejo
    ya se analizó (y puede ser de un pedido anterior)."""
    _, questions = evidence_request(SALIO, "en_camino", since_ms=1_001)

    assert [q.id for q in questions] == ["estado_pedido.evidencia.3", "estado_pedido.evidencia.5"]

    reading = reading_from(
        SALIO, _choice("en_camino", 0.93), _evidence(e1=0.99, e3=0.97, e5=0.1), since_ms=1_001
    )
    assert reading["verdict"]["evidence"] == ["Hola! ya salió con el mensajero,   llega hoy"], (
        "un mensaje viejo no cuenta como evidencia aunque Jev lo marque"
    )


def test_jev_seguro_del_cambio_y_de_la_evidencia_da_el_veredicto_con_la_forma_del_llm():
    reading = reading_from(SALIO, _choice("en_camino", 0.93), _evidence(e1=0.05, e3=0.97, e5=0.2))

    assert reading["verdict"] == {
        "action": "transition",
        "to_stage": "shipping",
        "evidence": ["Hola! ya salió con el mensajero,   llega hoy"],
        "confidence": "high",
    }, "la evidencia es el texto EXACTO del mensaje (la guarda del grafo lo exige)"
    assert reading["model"] == "typesafe/jev-1.13"
    assert reading["error"] is None
    assert reading["answers"] == [
        {"id": CHANGE_QID, "choice": "en_camino", "p": 0.93},
        {"id": "estado_pedido.evidencia.1", "p": 0.05},
        {"id": "estado_pedido.evidencia.3", "p": 0.97},
        {"id": "estado_pedido.evidencia.5", "p": 0.2},
    ]


def test_el_pago_confirmado_es_confirm_payment():
    convo = _convo([("customer", "ya pagué", False), ("human_operator", "Pago recibido, gracias!", False)], paid=False)

    reading = reading_from(convo, _choice("pago", 0.9), _evidence(e2=0.95))

    assert reading["verdict"] == {
        "action": "confirm_payment",
        "evidence": ["Pago recibido, gracias!"],
        "confidence": "high",
    }


def test_jev_seguro_de_que_nada_cambio_da_none_sin_preguntar_evidencia():
    reading = reading_from(SALIO, _choice("nada", 0.9))

    assert reading["verdict"] == {"action": "none", "evidence": [], "confidence": "high"}


def test_si_jev_duda_del_cambio_no_hay_veredicto_y_decide_el_llm():
    reading = reading_from(SALIO, _choice("en_camino", 0.6))

    assert reading["verdict"] is None
    assert reading["error"] is None


def test_si_ningun_mensaje_lo_prueba_con_certeza_no_hay_veredicto():
    reading = reading_from(SALIO, _choice("en_camino", 0.93), _evidence(e1=0.1, e3=0.7, e5=0.2))

    assert reading["verdict"] is None
    assert reading["error"] is None


def test_si_jev_cae_no_hay_veredicto_y_queda_el_motivo():
    failed = PerceptionResult(ok=False, error="timeout", model="typesafe/jev-1.13")

    first = reading_from(SALIO, failed)
    second = reading_from(SALIO, _choice("en_camino", 0.93), failed)

    assert (first["verdict"], first["error"]) == (None, "timeout")
    assert (second["verdict"], second["error"]) == (None, "timeout")


def test_lo_que_se_tapa_son_los_datos_personales_del_borrador_del_pedido_vinculado():
    metadata = {
        "tag": "HUMANO",
        "episodes": [
            {"order_draft": {"slots": {"nombre_recibe": "Otra Persona"}}},
            {
                "order_id": "order_01SALIO",
                "order_draft": {
                    "slots": {
                        "nombre_recibe": "Ana María Pérez",
                        "direccion": "Calle 10 # 20-30",
                        "barrio": "Chapinero",
                        "telefono": "3001234567",
                        "producto": "Vela de soya",
                    }
                },
            },
            {"closed_at_ms": None},
        ],
    }

    terms = redact_terms(metadata)

    assert {"Ana María Pérez", "Ana", "María", "Pérez", "Calle 10 # 20-30", "Chapinero", "3001234567"} <= set(terms)
    assert "Vela de soya" not in terms
    assert "Otra Persona" not in terms, "solo el borrador del episodio que tiene el pedido"
    assert redact_terms({"episodes": "roto"}) == ()
