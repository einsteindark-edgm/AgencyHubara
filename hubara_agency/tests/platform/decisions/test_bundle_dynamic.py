"""Lo que decide sobre listas que trae la entrada (PAQUETES_DE_DECISION.md F3).

Las capacidades B preguntan sobre listas que no se saben al escribir el
paquete (las categorías del catálogo, los colores de un producto, los ítems
del pedido) o solo cuando algo se cumple en la entrada. El paquete lo
declara y el certificador lo hace cumplir:

  options:   las opciones de un choice las arma un builtin desde la entrada;
             la tabla lee su valor como `opt['<pregunta>'][<opción>]`.
  when:      una pregunta se hace solo si la condición sobre la entrada se
             cumple (lee `inp` y `consts`, nunca respuestas).
  view:      campos que un builtin deriva de la entrada (`inp.campo`).
  control:   la capacidad cuyo interruptor comparte (una variante).
  tipos:     tuplas posicionales `(string, tuple<string>)` y uniones `A | B`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.platform.decisions import DOUBT, check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import CATALOG, _write

CATALOG3: dict[str, Any] = {
    **CATALOG,
    "inputs": {
        "Inbound": {"text": "string?", "now_ms": "int"},
        "Pedido": {"query": "string"},
    },
    "builtins": {
        **CATALOG["builtins"],
        "constant_empty": {"kind": "rule"},
        "same_any": {"kind": "same"},
        "jev_any": {"kind": "floor"},
        "catalog_categories": {"kind": "options", "input": "Pedido"},
        "purchase_window": {"kind": "view", "input": "Inbound",
                            "fields": {"context": "bool", "asked_known": "string?"}},
    },
    "constants": {"MIN_ENUMERATED": {"type": "int", "value": 4}},
}

CATEGORIA: dict[str, Any] = {
    "capability": "categoria",
    "value": "{categoria: string?}",
    "input": "Pedido",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "questions": [
        {"id": "categoria.cual", "kind": "choice", "text": "¿Cuál categoría?",
         "options": {"builtin": "catalog_categories"},
         "criteria": {"ambiguo": "más de una", "ninguno": "ninguna"}},
    ],
    "thresholds": {"choice": 0.80},
    "decide": [
        {"when": "!('categoria.cual' in choice) || conf['categoria.cual'] < th['choice'] || choice['categoria.cual'] == 'ambiguo'",
         "then": "doubt"},
        {"when": "choice['categoria.cual'] == 'ninguno'", "then": {"categoria": None}},
        {"when": "choice['categoria.cual'] in opt['categoria.cual']",
         "then": {"expr": "{'categoria': opt['categoria.cual'][choice['categoria.cual']]}"}},
        {"otherwise": "doubt"},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"categoria.cual": {"choice": "santos", "p": 0.9}},
         "options": {"categoria.cual": {"santos": "religiosas"}}, "expect": {"categoria": "religiosas"}},
        {"answers": {"categoria.cual": {"choice": "otra", "p": 0.9}},
         "options": {"categoria.cual": {"santos": "religiosas"}}, "expect": "doubt"},
        {"answers": {"categoria.cual": {"choice": "ninguno", "p": 0.9}}, "expect": {"categoria": None}},
    ],
}

COMPRA: dict[str, Any] = {
    "capability": "compra",
    "value": "list<string?>",
    "input": "Inbound",
    "view": {"builtin": "purchase_window"},
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "questions": [
        {"id": "compra.que_hace", "kind": "choice", "text": "¿Qué hace?",
         "criteria": {"confirma": "confirma", "otro": "otra cosa"}},
        {"id": "compra.pregunta_compra", "kind": "noul", "text": "¿Le preguntó?",
         "when": "inp.context && inp.asked_known == null",
         "criteria": {"true": "sí", "false": "no"}},
    ],
    "thresholds": {"choice": 0.70, "purchase_question": 0.85},
    "vars": {
        "asked": "inp.asked_known != null ? (inp.asked_known == 'confirmar_compra' ? 1.0 : 0.0)"
                 " : ('compra.pregunta_compra' in p ? p['compra.pregunta_compra'] : -1.0)",
    },
    "decide": [
        {"when": "!('compra.que_hace' in choice) || conf['compra.que_hace'] < th['choice']", "then": "doubt"},
        {"when": "choice['compra.que_hace'] != 'confirma'", "then": [None, "text"]},
        {"when": "vars.asked >= th['purchase_question']", "then": ["affirmation", "text"]},
        {"otherwise": "doubt"},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"compra.que_hace": {"choice": "confirma", "p": 0.9}},
         "input": {"context": True, "asked_known": "confirmar_compra"}, "expect": ["affirmation", "text"]},
        {"answers": {"compra.que_hace": {"choice": "confirma", "p": 0.9}, "compra.pregunta_compra": 0.9},
         "input": {"context": True, "asked_known": None}, "expect": ["affirmation", "text"]},
        {"answers": {"compra.que_hace": {"choice": "otro", "p": 0.9}}, "expect": [None, "text"]},
    ],
}

ENUMERACION: dict[str, Any] = {
    "capability": "enumeracion",
    "value": "() | (string, tuple<string>)",
    "input": "Inbound",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "questions": [
        {"id": "enumeracion.que", "kind": "choice", "text": "¿Qué enumera?",
         "criteria": {"aromas": "aromas", "nada": "nada"}},
    ],
    "thresholds": {"confidence": 0.85},
    "decide": [
        {"when": "!('enumeracion.que' in choice) || conf['enumeracion.que'] < th['confidence']", "then": "doubt"},
        {"when": "choice['enumeracion.que'] == 'aromas'", "then": {"expr": "['scent', ['Lavanda', 'Vainilla']]"}},
        {"otherwise": []},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"enumeracion.que": {"choice": "aromas", "p": 0.9}}, "expect": ["scent", ["Lavanda", "Vainilla"]]},
        {"answers": {"enumeracion.que": {"choice": "nada", "p": 0.9}}, "expect": []},
    ],
}


def _codes(tmp_path: Path, capabilities: dict[str, dict[str, Any]]) -> list[str]:
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG3)
    return [str(d) for d in check_bundle(bundle_dir, catalog_path)]


def _load(tmp_path: Path, capabilities: dict[str, dict[str, Any]]):  # noqa: ANN202
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG3)
    return load_bundle(bundle_dir, catalog_path)


def test_the_options_come_from_the_input_and_the_table_reads_their_value(tmp_path: Path) -> None:
    table = _load(tmp_path, {"categoria": CATEGORIA}).capability("categoria")
    options = {"categoria.cual": {"santos": "religiosas", "aromas": "aromaticas"}}

    assert table.decide(answers={"categoria.cual": ("aromas", 0.9)}, options=options) == {"categoria": "aromaticas"}
    assert table.decide(answers={"categoria.cual": ("ninguno", 0.9)}, options=options) == {"categoria": None}
    assert table.decide(answers={"categoria.cual": ("inventada", 0.9)}, options=options) is DOUBT
    assert table.decide(answers={"categoria.cual": ("santos", 0.79)}, options=options) is DOUBT
    assert table.option_questions == ("categoria.cual",)


def test_a_question_is_asked_only_when_its_condition_holds(tmp_path: Path) -> None:
    table = _load(tmp_path, {"compra": COMPRA}).capability("compra")

    assert [q.id for q in table.questions_for({"context": True, "asked_known": None})] == [
        "compra.que_hace", "compra.pregunta_compra",
    ]
    assert [q.id for q in table.questions_for({"context": True, "asked_known": "confirmar_compra"})] == ["compra.que_hace"]
    assert [q.id for q in table.questions_for({"context": False, "asked_known": None})] == ["compra.que_hace"]


def test_the_view_fields_are_readable_as_input(tmp_path: Path) -> None:
    table = _load(tmp_path, {"compra": COMPRA}).capability("compra")

    assert set(table.input_fields) == {"text", "now_ms", "context", "asked_known"}
    got = table.decide(answers={"compra.que_hace": ("confirma", 0.9)}, inp={"context": True, "asked_known": "confirmar_compra"})
    assert got == ["affirmation", "text"]


def test_positional_tuples_and_unions_keep_their_python_shape(tmp_path: Path) -> None:
    table = _load(tmp_path, {"enumeracion": ENUMERACION}).capability("enumeracion")

    got = table.decide(answers={"enumeracion.que": ("aromas", 0.9)})
    assert got == ("scent", ("Lavanda", "Vainilla"))
    assert isinstance(got[1], tuple)
    assert table.decide(answers={"enumeracion.que": ("nada", 0.9)}) == ()


def test_a_variant_shares_the_control_of_its_capability(tmp_path: Path) -> None:
    from tests.platform.decisions.test_bundle_checker import BAJA

    bundle = _load(tmp_path, {"baja": BAJA, "baja_plantilla": {**BAJA, "capability": "baja_plantilla", "control": "baja"}})

    assert bundle.capability("baja_plantilla").control == "baja"
    assert bundle.capability("baja").control == "baja"


def test_the_new_shapes_certify(tmp_path: Path) -> None:
    assert _codes(tmp_path, {"categoria": CATEGORIA, "compra": COMPRA, "enumeracion": ENUMERACION}) == []


@pytest.mark.parametrize(
    ("capability", "changes", "code"),
    [
        # `opt` de una pregunta sin opciones de la entrada
        ("compra", {"decide": [{"when": "'x' in opt['compra.que_hace']", "then": "doubt"}, {"otherwise": "doubt"}]}, "DB005"),
        # opciones con un builtin que no es de opciones
        ("categoria", {"questions": [{**CATEGORIA["questions"][0], "options": {"builtin": "jev_any"}}]}, "DB004"),
        # opciones en una pregunta sí/no
        ("compra", {"questions": [COMPRA["questions"][0],
                                  {**COMPRA["questions"][1], "options": {"builtin": "catalog_categories"}}]}, "DB001"),
        # la condición de una pregunta no lee respuestas (todavía no hay)
        ("compra", {"questions": [COMPRA["questions"][0],
                                  {**COMPRA["questions"][1], "when": "'compra.que_hace' in choice"}]}, "DB005"),
        # la condición de una pregunta da true o false
        ("compra", {"questions": [COMPRA["questions"][0], {**COMPRA["questions"][1], "when": "inp.asked_known"}]}, "DB006"),
        # un campo que la vista no da
        ("compra", {"vars": {"asked": "inp.asked == null ? 1.0 : 0.0"}}, "DB005"),
        # una vista que no es de la entrada de la capacidad
        ("categoria", {"view": {"builtin": "purchase_window"}}, "DB004"),
        # tupla posicional con otra aridad
        ("enumeracion", {"decide": [{"when": "'enumeracion.que' in choice", "then": ["scent"]}, {"otherwise": []}]}, "DB007"),
        # un ejemplo con opciones de una pregunta sin opciones
        ("compra", {"examples": [{**COMPRA["examples"][2], "options": {"compra.que_hace": {"a": "b"}}}]}, "DB010"),
        # la variante de una capacidad que no está en el paquete
        ("compra", {"control": "pago"}, "DB003"),
    ],
)
def test_each_mistake_is_rejected(tmp_path: Path, capability: str, changes: dict[str, Any], code: str) -> None:
    spec = {"categoria": CATEGORIA, "compra": COMPRA, "enumeracion": ENUMERACION}[capability]

    assert any(d.startswith(code) for d in _codes(tmp_path, {capability: {**spec, **changes}}))
