"""Valores con forma, salidas calculadas, variables y la entrada (PAQUETES_DE_DECISION.md F2).

Una capacidad no siempre decide un bool: `cantidad` da `{cantidad: int?}`,
`retoma` da `{deferral: {kind, until_ms}?, courtesy: bool}`. El tipo del
valor se declara (`value:`) y el certificador lo hace cumplir: un `then`
literal que no es del tipo no compila, un `then: {expr: …}` se calcula con
CEL, `vars` nombra cálculos intermedios, `inp.campo` lee la entrada declarada
en el catálogo y `consts.NOMBRE` las constantes del motor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.platform.decisions import DOUBT, check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import CATALOG, _write

CATALOG2: dict[str, Any] = {
    **CATALOG,
    "inputs": {
        "Inbound": {"text": "string?", "now_ms": "int"},
        "Reply": {"last_agent_text": "string?", "text": "string?", "open_slot": "bool"},
    },
    "builtins": {
        **CATALOG["builtins"],
        "constant_empty": {"kind": "rule"},
        "same_any": {"kind": "same"},
        "jev_any": {"kind": "floor"},
    },
    "constants": {"OPEN_DEFERRAL_MS": {"type": "int", "value": 1000}, "OPEN_KIND": {"type": "string", "value": "open"}},
}

CANTIDAD: dict[str, Any] = {
    "capability": "cantidad",
    "value": "{cantidad: int?}",
    "input": "Reply",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "questions": [
        {"id": "cantidad.pregunto", "kind": "noul", "text": "¿Preguntó?", "criteria": {"true": "sí", "false": "no"}},
        {"id": "cantidad.dio", "kind": "choice", "text": "¿Cuántas?",
         "criteria": {"1": "una", "2": "dos", "otra": "otra", "ninguna": "ninguna"}},
    ],
    "thresholds": {"asked": 0.85, "not_asked": 0.15, "quantity": 0.85},
    "decide": [
        {"when": "!('cantidad.pregunto' in p)", "then": "doubt"},
        {"when": "p['cantidad.pregunto'] <= th['not_asked']", "then": {"cantidad": None}},
        {"when": "'cantidad.dio' in choice && choice['cantidad.dio'].matches('^[0-9]+$') && conf['cantidad.dio'] >= th['quantity']",
         "then": {"expr": "{'cantidad': int(choice['cantidad.dio'])}"}},
        {"otherwise": "doubt"},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"cantidad.pregunto": 0.9, "cantidad.dio": {"choice": "2", "p": 0.9}}, "expect": {"cantidad": 2}},
        {"answers": {"cantidad.pregunto": 0.1}, "expect": {"cantidad": None}},
        {"answers": {"cantidad.pregunto": 0.9, "cantidad.dio": {"choice": "otra", "p": 0.9}}, "expect": "doubt"},
        {"answers": {}, "expect": "doubt"},
    ],
}

RETOMA: dict[str, Any] = {
    "capability": "retoma",
    "value": "{deferral: {kind: string, until_ms: int}?, courtesy: bool}",
    "input": "Inbound",
    "rule": {"builtin": "constant_empty"},
    "state": None,
    "questions": [
        {"id": "retoma.aplaza", "kind": "noul", "text": "¿Aplaza?", "criteria": {"true": "sí", "false": "no"}},
        {"id": "retoma.cortesia", "kind": "noul", "text": "¿Cortesía?", "criteria": {"true": "sí", "false": "no"}},
    ],
    "thresholds": {"yes": 0.85, "no": 0.15},
    "vars": {
        "courtesy": "p['retoma.cortesia'] >= th['yes'] ? true : (p['retoma.cortesia'] <= th['no'] ? false : rule.courtesy == true)",
    },
    "decide": [
        {"when": "!('retoma.aplaza' in p) || !('retoma.cortesia' in p)", "then": "doubt"},
        {"when": "p['retoma.aplaza'] <= th['no']", "then": {"expr": "{'deferral': null, 'courtesy': vars.courtesy}"}},
        {"when": "p['retoma.aplaza'] < th['yes']", "then": "doubt"},
        {"otherwise": {"expr": "{'deferral': {'kind': consts.OPEN_KIND, 'until_ms': inp.now_ms + consts.OPEN_DEFERRAL_MS}, 'courtesy': vars.courtesy}"}},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"retoma.aplaza": 0.9, "retoma.cortesia": 0.9}, "input": {"text": "mañana", "now_ms": 5},
         "expect": {"deferral": {"kind": "open", "until_ms": 1005}, "courtesy": True}},
        {"answers": {"retoma.aplaza": 0.1, "retoma.cortesia": 0.5}, "rule": {"deferral": None, "courtesy": True},
         "input": {"text": "ok", "now_ms": 5}, "expect": {"deferral": None, "courtesy": True}},
        {"answers": {"retoma.cortesia": 0.5}, "rule": {"deferral": None, "courtesy": True}, "expect": "doubt"},
        {"answers": {"retoma.aplaza": 0.5, "retoma.cortesia": 0.5}, "rule": {"deferral": None, "courtesy": True},
         "expect": "doubt"},
    ],
}


def _codes(tmp_path: Path, capabilities: dict[str, dict[str, Any]]) -> list[str]:
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG2)
    return [d.code for d in check_bundle(bundle_dir, catalog_path)]


def _load(tmp_path: Path, capabilities: dict[str, dict[str, Any]]):  # noqa: ANN202
    bundle_dir, catalog_path = _write(tmp_path, capabilities, catalog=CATALOG2)
    return load_bundle(bundle_dir, catalog_path)


def test_a_record_value_comes_from_a_literal_or_an_expression(tmp_path: Path) -> None:
    table = _load(tmp_path, {"cantidad": CANTIDAD}).capability("cantidad")

    yes = {"cantidad.pregunto": 0.9}
    assert table.decide(answers={**yes, "cantidad.dio": ("2", 0.9)}) == {"cantidad": 2}
    assert table.decide(answers={"cantidad.pregunto": 0.1}) == {"cantidad": None}
    assert table.decide(answers={**yes, "cantidad.dio": ("otra", 0.9)}) is DOUBT


def test_vars_input_and_constants_feed_the_decision(tmp_path: Path) -> None:
    table = _load(tmp_path, {"retoma": RETOMA}).capability("retoma")

    got = table.decide(answers={"retoma.aplaza": 0.9, "retoma.cortesia": 0.1}, inp={"text": "x", "now_ms": 7})

    assert got == {"deferral": {"kind": "open", "until_ms": 1007}, "courtesy": False}


def test_reading_an_answer_that_did_not_come_is_doubt_not_the_next_row(tmp_path: Path) -> None:
    """Una llave que no está devuelve un VALOR de error en CEL (no lanza):
    sin esta guarda, la fila se saltaba en silencio y decidía la siguiente."""
    from tests.platform.decisions.test_bundle_checker import BAJA

    rows = [{"when": "p['baja.pide'] >= th['yes']", "then": True}, {"otherwise": False}]
    examples = [{"answers": {"baja.pide": 0.9}, "expect": True}]
    table = _load(tmp_path, {"baja": {**BAJA, "decide": rows, "examples": examples}}).capability("baja")

    assert table.decide(answers={}) is DOUBT


def test_tuples_and_lists_keep_their_python_shape(tmp_path: Path) -> None:
    lista = {
        **CANTIDAD, "capability": "lista", "value": "tuple<string>",
        "decide": [{"when": "'cantidad.pregunto' in p", "then": {"expr": "['a', 'b']"}}, {"otherwise": []}],
        "examples": [{"answers": {"cantidad.pregunto": 0.5}, "expect": ["a", "b"]}, {"answers": {}, "expect": []}],
    }
    table = _load(tmp_path, {"lista": lista}).capability("lista")

    assert table.decide(answers={"cantidad.pregunto": 0.5}) == ("a", "b")
    assert table.decide(answers={}) == ()


@pytest.mark.parametrize(
    ("capability", "changes", "code"),
    [
        ("cantidad", {"value": "{cantidad: integer}"}, "DB001"),                       # tipo inventado
        ("cantidad", {"value": "{cantidad: int?"}, "DB001"),                           # llave sin cerrar
        ("cantidad", {"decide": [{"when": "'cantidad.pregunto' in p", "then": {"cantidad": "dos"}},
                                 {"otherwise": "doubt"}]}, "DB007"),                   # literal de otro tipo
        ("cantidad", {"decide": [{"when": "'cantidad.pregunto' in p", "then": {"numero": 2}},
                                 {"otherwise": "doubt"}]}, "DB007"),                   # campo que no existe
        ("retoma", {"vars": {"courtesy": "vars.otra == true"}}, "DB005"),              # var no declarada (aún)
        ("retoma", {"decide": [{"when": "inp.telefono == ''", "then": "doubt"},
                               {"otherwise": "doubt"}]}, "DB005"),                    # campo de entrada no declarado
        ("retoma", {"decide": [{"when": "consts.MAX > 2", "then": "doubt"},
                               {"otherwise": "doubt"}]}, "DB005"),                    # constante no declarada
        ("cantidad", {"decide": [{"when": "'cantidad.pregunto' in p", "then": {"expr": "{'cantidad': 'dos'}"}},
                                 {"otherwise": "doubt"}],
                      "examples": [{"answers": {"cantidad.pregunto": 0.9}, "expect": {"cantidad": 2}}]}, "DB010"),
        ("retoma", {"examples": [{"answers": {"retoma.aplaza": 0.9, "retoma.cortesia": 0.9},
                                  "input": {"texto": "x"}, "expect": "doubt"}]}, "DB010"),  # entrada con campo inventado
    ],
)
def test_typed_values_are_certified(tmp_path: Path, capability: str, changes: dict[str, Any], code: str) -> None:
    spec = {"cantidad": CANTIDAD, "retoma": RETOMA}[capability]

    assert code in _codes(tmp_path, {capability: {**spec, **changes}})


def test_the_examples_pass(tmp_path: Path) -> None:
    assert _codes(tmp_path, {"cantidad": CANTIDAD, "retoma": RETOMA}) == []
