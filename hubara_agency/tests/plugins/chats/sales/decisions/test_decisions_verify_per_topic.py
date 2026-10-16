"""③ por asunto: la pregunta, lo que ve y el complemento (ventas-7, 2026-10-13).

Sonda con Jev real sobre el turno 1 del anuncio de velitas (16:53 Bogotá): con
la pregunta de siempre («¿atiende el asunto catálogo?») los botones daban 0,55
y el catálogo completo, 0,72 («sí»). Con la del catálogo («¿le da justo lo que
pidió?»): botones 0,10, catálogo completo 0,36 y la lista de las velitas 0,82
si ③ veía qué productos llevaba (0,58 si no). La pregunta precisa aplicada a
todos los asuntos marcaba el saludo como faltante: va por asunto.

Y aunque ③ dijera «falta el catálogo», la burbuja de más era un texto con
`send_reply`, que no muestra productos: el asunto trae su propio complemento.

Sin las llaves nuevas (ventas-6) nada cambia: la misma pregunta, el mismo
`state` y el mismo complemento.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan
from src.plugins.chats.agent.sales.decisions.policies import turno_v1
from src.plugins.chats.agent.sales.decisions.questionnaire import questionnaire_of
from src.plugins.chats.shared.store_pack import BUNDLES_DIR

V6_TURN = BUNDLES_DIR / "ventas-6" / "turn.yaml"
if not V6_TURN.is_file():  # en un clon de forge solo viajan `ventas` y el paquete de la tienda
    pytest.skip("clon de forge: ventas-6 (experimento de esta tienda) no viaja", allow_module_level=True)
V6_RAW = yaml.safe_load(V6_TURN.read_text(encoding="utf-8"))["questionnaire"]
CATALOG_VERIFY = {
    "text": "¿La respuesta le MUESTRA al cliente los productos que pidió{where}",
    "criteria": {"true": "sí: se los muestra", "false": "no: solo le pregunta o le ofrece verlos"},
}
COMPLEMENT = "Muéstrale con present_products los productos que pidió."
MSGS = [{"text": "¡Hola! Quiero más información sobre las velitas del 7 de diciembre.", "ts_ms": 1_791_928_409_000}]
CONTEXT = TurnContext(window=Window(lines=("[asesor] ¿Buscas las velitas para una ocasión especial?",)), facts=())
CONTENTS = ["Botones: «Ver catálogo» · «Es para una ocasión»"]


def _v7_like() -> Any:
    raw = copy.deepcopy(V6_RAW)
    for topic in raw["topics"]:
        if topic["id"] == "catalogo":
            topic["verify"] = CATALOG_VERIFY
            topic["complement"] = COMPLEMENT
    raw["reply_state"] = {**raw["reply_state"], "contents": "Lo que muestran las tarjetas: {contents}", "with_context": True}
    return questionnaire_of(raw)


PLAN = TurnPlan(ok=True, topics=(PlanTopic("catalogo"), PlanTopic("saludo")))


def test_the_catalog_has_its_own_question_and_the_rest_keep_the_common_one() -> None:
    v6, v7 = questionnaire_of(V6_RAW), _v7_like()

    catalogo, saludo = v7.verify_questions(PLAN)
    assert catalogo.text == "¿La respuesta le MUESTRA al cliente los productos que pidió que planteó el cliente?"
    assert dict(catalogo.criteria) == CATALOG_VERIFY["criteria"]
    assert saludo == v6.verify_questions(PLAN)[1]


def test_without_its_own_question_every_topic_keeps_the_common_one() -> None:
    v6 = questionnaire_of(V6_RAW)

    catalogo, _ = v6.verify_questions(PLAN)
    assert catalogo.text == "¿La respuesta del asesor (texto y tarjetas) atiende el asunto «catálogo» que planteó el cliente?"


def test_the_verification_sees_what_the_cards_show_and_the_context_when_the_bundle_asks() -> None:
    state = _v7_like().reply_state(MSGS, "¿Buscas las velitas para una ocasión especial?", ["send_quick_replies"],
                                   contents=CONTENTS, context=CONTEXT)

    lines = state.splitlines()
    assert lines[0].startswith("CONTEXTO") and "[asesor] ¿Buscas las velitas" in lines[1]
    assert lines[-1] == "Lo que muestran las tarjetas: Botones: «Ver catálogo» · «Es para una ocasión»"


def test_ventas_6_sees_the_same_as_before_even_if_contents_and_context_travel() -> None:
    v6 = questionnaire_of(V6_RAW)
    before = v6.reply_state(MSGS, "Hola", ["send_quick_replies"])

    assert v6.reply_state(MSGS, "Hola", ["send_quick_replies"], contents=CONTENTS, context=CONTEXT) == before


def test_a_missing_catalog_complement_shows_the_products_instead_of_a_text() -> None:
    note = turno_v1.complement_note(TurnPlan(ok=True, topics=(PlanTopic("catalogo"),)), ["catalogo"], _v7_like())

    assert COMPLEMENT in note and "send_reply" not in note


def test_with_another_missing_topic_that_one_still_goes_by_text() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo"), PlanTopic("precio")))

    note = turno_v1.complement_note(plan, ["catalogo", "precio"], _v7_like())

    assert COMPLEMENT in note and "send_reply" in note


def test_without_its_own_complement_the_note_is_the_same_as_before() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo"),))

    assert turno_v1.complement_note(plan, ["catalogo"], questionnaire_of(V6_RAW)) == (
        "[SISTEMA] Complemento del turno: el cliente también preguntó por catálogo y la respuesta que ya le "
        "enviaste no lo cubrió. Responde SOLO eso, breve, en un único mensaje con send_reply. No repitas lo "
        "que ya le enviaste."
    )


def test_the_raw_of_the_bundle_is_not_touched() -> None:
    assert "verify" not in next(t for t in V6_RAW["topics"] if t["id"] == "catalogo")
    assert Path(BUNDLES_DIR / "ventas-6" / "turn.yaml").is_file()
