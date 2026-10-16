"""③ por asunto en el turno del paquete (ventas-7, 2026-10-13).

La verificación hacía UNA pregunta para todos los asuntos («¿la respuesta
atiende el asunto X?»), sin ver qué mostraban las tarjetas ni lo que se habló
antes. Con el anuncio de velitas, mandar el catálogo completo a quien pidió las
velitas daba «sí» (0,72) y unos botones «Ver catálogo», duda (0,55).

El paquete puede ahora:

* darle a un asunto su propia pregunta de ③ (`verify.text`, `verify.criteria`)
  y su complemento (`complement`): el del catálogo es «le MUESTRA los
  productos que pidió», y si falta, la burbuja de más los muestra;
* pedir que ③ vea lo que muestran las tarjetas (`reply_state.contents`, con
  `{contents}`) y el contexto del turno (`reply_state.with_context`).

Sin esas llaves, todo queda como antes (ventas-6 no cambia).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.platform.decisions.test_bundle_turn import TURN, _compiled, _diagnostics, _turn, _with

CATALOG_VERIFY = {
    "text": "¿La respuesta le MUESTRA al cliente los productos que pidió{where}",
    "criteria": {"true": "sí: se los muestra", "false": "no: solo le pregunta o le ofrece verlos"},
}
COMPLEMENT = "Muéstrale los productos que pidió con show_catalog."


def _per_topic() -> dict[str, Any]:
    topics = [dict(t) for t in TURN["questionnaire"]["topics"]]
    topics[0] = {**topics[0], "verify": CATALOG_VERIFY, "complement": COMPLEMENT}
    turn = _with("questionnaire.topics", topics)
    turn["questionnaire"]["reply_state"] = {
        **turn["questionnaire"]["reply_state"], "contents": "Lo que muestran: {contents}", "with_context": True,
    }
    return turn


def test_a_topic_with_its_own_verification_and_complement_compiles(tmp_path: Path) -> None:
    turn = _compiled(tmp_path, _per_topic())

    catalogo = turn.questionnaire["topics"][0]
    assert catalogo["verify"] == CATALOG_VERIFY and catalogo["complement"] == COMPLEMENT
    assert turn.questionnaire["reply_state"]["contents"] == "Lo que muestran: {contents}"
    assert turn.questionnaire["reply_state"]["with_context"] is True


def test_without_the_new_keys_the_turn_is_the_same(tmp_path: Path) -> None:
    turn = _compiled(tmp_path)

    assert "verify" not in turn.questionnaire["topics"][0]
    assert set(turn.questionnaire["reply_state"]) == {"header", "empty", "components"}


def _topic0(**fields: Any) -> dict[str, Any]:
    turn = _per_topic()
    turn["questionnaire"]["topics"][0] = {**turn["questionnaire"]["topics"][0], **fields}
    return turn


def _reply_state(**fields: Any) -> dict[str, Any]:
    turn = _per_topic()
    turn["questionnaire"]["reply_state"] = {**turn["questionnaire"]["reply_state"], **fields}
    return turn


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        # La pregunta del asunto solo llena `{label}` y `{where}`.
        (_topic0(verify={**CATALOG_VERIFY, "text": "¿Le muestra {productos}{where}"}),
         "DB005 turn.yaml: questionnaire.topics[0].verify.text"),
        # Es sí/no: criteria con exactamente true y false.
        (_topic0(verify={**CATALOG_VERIFY, "criteria": {"si": "sí", "no": "no"}}), "DB001 turn.yaml: questionnaire.topics[0].verify"),
        # El complemento es texto para el modelo: sin plantillas.
        (_topic0(complement="Muéstrale {productos}."), "DB005 turn.yaml: questionnaire.topics[0].complement"),
        # Lo que muestran las tarjetas va en `{contents}`.
        (_reply_state(contents="Lo que muestran: {tarjetas}"), "DB005 turn.yaml: questionnaire.reply_state.contents"),
        # Llave desconocida.
        (_topic0(verify_hint="x"), "DB001 turn.yaml: questionnaire.topics[0].verify_hint"),
    ],
)
def test_what_does_not_compile(tmp_path: Path, turn: dict[str, Any], expected: str) -> None:
    diagnostics = _diagnostics(tmp_path, turn)

    assert any(d.startswith(expected) for d in diagnostics), diagnostics


def test_the_base_turn_still_compiles(tmp_path: Path) -> None:
    assert _diagnostics(tmp_path, _turn()) == []
