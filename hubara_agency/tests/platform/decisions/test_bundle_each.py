"""Decidir ítem por ítem (PAQUETES_DE_DECISION.md F4, las C y el egreso por partes).

Persona, monto, datos, fuera de catálogo y las del egreso por partes le
preguntan a Jev lo mismo sobre cada oración, párrafo, término o dato. El
paquete lo declara una vez:

  items:   un builtin (clase `items`) arma la lista desde la entrada; cada
           ítem es un registro con los campos que declara el catálogo.
  each:    la pregunta de cada ítem: `id` y `text` son plantillas con
           `{n}` (posición desde 1), `{index}` (desde 0) y los campos del
           ítem; `when` (sobre `item`, `inp`, `consts`) elige qué ítems se
           preguntan; el texto puede tener variantes por condición.
  tabla:   lee `items`: cada ítem con sus campos, `n`, `index` y, si Jev
           contestó, `p` (sí/no) o `choice` y `conf` (opciones). Se recorre
           con `filter`, `map`, `exists`, `all` y `join`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.platform.decisions import DOUBT, check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import CATALOG, _write

CATALOG4: dict[str, Any] = {
    **CATALOG,
    "inputs": {"Frases": {"text": "string"}, "Datos": {}},
    "builtins": {
        **CATALOG["builtins"],
        "constant_empty": {"kind": "rule"},
        "same_any": {"kind": "same"},
        "jev_any": {"kind": "floor"},
        "parts": {"kind": "items", "input": "Frases", "fields": {"text": "string"}},
        "slot_values": {"kind": "items", "input": "Datos",
                        "fields": {"slot": "string", "value": "string", "ask": "bool", "personal": "bool"}},
    },
}

PERSONA: dict[str, Any] = {
    "capability": "persona",
    "value": "tuple<int>",
    "input": "Frases",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "items": {"builtin": "parts"},
    "each": {"id": "persona.{n}", "kind": "noul", "text": "¿La oración [{n}] delata un bot?",
             "criteria": {"true": "sí", "false": "no"}},
    "thresholds": {"yes": 0.85, "no": 0.15},
    "decide": [
        {"when": "!items.exists(i, has(i.p))", "then": "doubt"},
        {"otherwise": {"expr": "items.filter(i, (has(i.p) && i.p >= th['yes']) || "
                               "(!(has(i.p) && i.p <= th['no']) && i.index in rule)).map(i, i.index)"}},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"items": [{"text": "Soy un bot", "p": 0.9}, {"text": "Hola", "p": 0.1}], "expect": [0]},
        {"items": [{"text": "a"}, {"text": "b"}], "expect": "doubt"},
        {"items": [{"text": "a", "p": 0.5}, {"text": "b", "p": 0.1}], "rule": [0], "expect": [0]},
    ],
}

DATOS: dict[str, Any] = {
    "capability": "datos",
    "value": "tuple<string>",
    "input": "Datos",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "items": {"builtin": "slot_values"},
    "each": {
        "id": "datos.{slot}", "kind": "noul", "when": "item.ask",
        "text": [
            {"when": "item.personal", "text": "¿El cliente dio el dato «{slot}»? (tapado)"},
            {"otherwise": "¿El cliente dio este dato? {slot}: «{value}»"},
        ],
        "criteria": {"true": "sí", "false": "no"},
    },
    "thresholds": {"no": 0.15},
    "decide": [
        {"when": "!items.exists(i, has(i.p))", "then": "doubt"},
        {"otherwise": {"expr": "items.filter(i, has(i.p) && i.p <= th['no']).map(i, i.slot)"}},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"items": [{"slot": "ciudad", "value": "Cali", "ask": True, "personal": False, "p": 0.1}], "expect": ["ciudad"]},
    ],
}

RESCATE: dict[str, Any] = {
    **PERSONA,
    "capability": "rescate",
    "value": "string",
    "each": {"id": "rescate.{n}", "kind": "choice", "text": "¿Qué es el párrafo [{n}]?",
             "criteria": {"cliente": "para el cliente", "interno": "interno"}},
    "thresholds": {"choice": 0.8},
    "decide": [
        {"when": "items.exists(i, !has(i.choice) || i.conf < th['choice'])", "then": "doubt"},
        {"otherwise": {"expr": "items.filter(i, i.choice == 'cliente').map(i, i.text).join('\\n\\n')"}},
    ],
    "examples": [
        {"items": [{"text": "Hola", "choice": "cliente", "conf": 0.9}, {"text": "ESTADO", "choice": "interno", "conf": 0.9},
                   {"text": "Chao", "choice": "cliente", "conf": 0.8}], "expect": "Hola\n\nChao"},
        {"items": [{"text": "Hola", "choice": "cliente", "conf": 0.79}], "expect": "doubt"},
    ],
}


def _codes(tmp_path: Path, capabilities: dict[str, dict[str, Any]]) -> list[str]:
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG4)
    return [str(d) for d in check_bundle(bundle_dir, catalog_path)]


def _load(tmp_path: Path, capabilities: dict[str, dict[str, Any]]):  # noqa: ANN202
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG4)
    return load_bundle(bundle_dir, catalog_path)


def test_one_question_per_item_from_a_template(tmp_path: Path) -> None:
    table = _load(tmp_path, {"persona": PERSONA}).capability("persona")

    questions = table.each_questions([{"text": "Soy un bot"}, {"text": "Hola"}])

    assert [(q.id, q.kind, q.text) for q in questions] == [
        ("persona.1", "noul", "¿La oración [1] delata un bot?"),
        ("persona.2", "noul", "¿La oración [2] delata un bot?"),
    ]
    assert dict(questions[0].criteria) == {"true": "sí", "false": "no"}


def test_the_table_reads_each_item_with_its_answer(tmp_path: Path) -> None:
    table = _load(tmp_path, {"persona": PERSONA}).capability("persona")
    items = [{"text": "Soy un bot"}, {"text": "Hola"}, {"text": "Te paso con alguien"}]

    assert table.decide(answers={"persona.1": 0.9, "persona.2": 0.1}, items=items, rule=(2,)) == (0, 2)
    assert table.decide(answers={"persona.1": 0.5, "persona.2": 0.1, "persona.3": 0.1}, items=items, rule=(0,)) == (0,)
    assert table.decide(answers={}, items=items) is DOUBT


def test_only_the_items_whose_condition_holds_are_asked_and_the_text_has_variants(tmp_path: Path) -> None:
    table = _load(tmp_path, {"datos": DATOS}).capability("datos")
    items = [
        {"slot": "ciudad", "value": "Cali", "ask": True, "personal": False},
        {"slot": "direccion", "value": "Cra 1", "ask": True, "personal": True},
        {"slot": "telefono", "value": "3001234567", "ask": False, "personal": True},
    ]

    questions = table.each_questions(items)

    assert [(q.id, q.text) for q in questions] == [
        ("datos.ciudad", "¿El cliente dio este dato? ciudad: «Cali»"),
        ("datos.direccion", "¿El cliente dio el dato «direccion»? (tapado)"),
    ]
    # Lo que no se preguntó también se lee si llegó (la tabla decide sobre todos).
    got = table.decide(answers={"datos.ciudad": 0.9, "datos.telefono": 0.1}, items=items)
    assert got == ("telefono",)


def test_a_choice_per_item_and_join(tmp_path: Path) -> None:
    table = _load(tmp_path, {"rescate": RESCATE}).capability("rescate")
    items = [{"text": "Hola"}, {"text": "ESTADO: interesado"}]

    got = table.decide(answers={"rescate.1": ("cliente", 0.9), "rescate.2": ("interno", 0.95)}, items=items)

    assert got == "Hola"
    assert table.decide(answers={"rescate.1": ("cliente", 0.9)}, items=items) is DOUBT


def test_the_each_shapes_certify(tmp_path: Path) -> None:
    assert _codes(tmp_path, {"persona": PERSONA, "datos": DATOS, "rescate": RESCATE}) == []


@pytest.mark.parametrize(
    ("capability", "changes", "code"),
    [
        # placeholder que no es campo del ítem
        ("persona", {"each": {**PERSONA["each"], "text": "¿La oración [{nn}]?"}}, "DB005"),
        ("persona", {"each": {**PERSONA["each"], "id": "persona.{texto}"}}, "DB005"),
        # la condición de un ítem no lee respuestas
        ("persona", {"each": {**PERSONA["each"], "when": "'persona.1' in p"}}, "DB005"),
        # la condición de un ítem da true o false
        ("persona", {"each": {**PERSONA["each"], "when": "item.text"}}, "DB006"),
        # items con un builtin que no es de ítems
        ("persona", {"items": {"builtin": "jev_any"}}, "DB004"),
        # items de otra entrada
        ("persona", {"items": {"builtin": "slot_values"}}, "DB004"),
        # each sin items
        ("persona", {"items": None}, "DB001"),
        # un ejemplo con un campo de ítem inventado
        ("persona", {"examples": [{"items": [{"texto": "a", "p": 0.9}], "expect": [0]}]}, "DB010"),
        # `items` en una capacidad sin ítems
        ("persona", {"items": None, "each": None,
                     "questions": [{"id": "persona.x", "kind": "noul", "text": "¿?", "criteria": {"true": "sí", "false": "no"}}]},
         "DB006"),
    ],
)
def test_each_mistake_is_rejected(tmp_path: Path, capability: str, changes: dict[str, Any], code: str) -> None:
    spec = {"persona": PERSONA, "datos": DATOS, "rescate": RESCATE}[capability]
    # `None` en el cambio = quitar esa llave.
    spec = {k: v for k, v in {**spec, **changes}.items() if not (k in changes and v is None)}

    assert any(d.startswith(code) for d in _codes(tmp_path, {capability: spec}))
