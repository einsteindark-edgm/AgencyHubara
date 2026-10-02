"""El certificador de paquetes de decisión: «¿compila?» (PAQUETES_DE_DECISION.md §6).

Un paquete (YAML tipado + condiciones CEL) se rechaza antes de desplegar si
algo no cuadra. Estos casos son los errores típicos de quien escribe un
paquete (persona o agente): llaves inventadas, tipos equivocados, preguntas
o umbrales que no existen, condiciones que no compilan y ejemplos que no dan
lo esperado. Cada error sale con su código y la ruta exacta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.decisions import DOUBT, check_bundle, load_bundle

CATALOG = {
    "engine_contract": 1,
    "inputs": ["Inbound"],
    "builtins": {
        "constant_false": {"kind": "rule", "value": "bool"},
        "opt_out_text": {"kind": "rule", "value": "bool", "input": "Inbound"},
        "customer_message": {"kind": "state", "input": "Inbound", "params": {"header": "str"}},
        "jev": {"kind": "floor"},
        "rule_or_jev": {"kind": "floor", "value": "bool"},
        "bool_eq": {"kind": "same", "value": "bool"},
    },
    "required_floors": {"baja": "rule_or_jev"},
}

BAJA: dict[str, Any] = {
    "capability": "baja",
    "value": "bool",
    "input": "Inbound",
    "rule": {"builtin": "opt_out_text"},
    "state": {"builtin": "customer_message", "with": {"header": "Mensaje del cliente:"}},
    "questions": [
        {"id": "baja.pide", "kind": "noul", "text": "¿Pide dejar de recibir mensajes?",
         "criteria": {"true": "sí", "false": "no"}},
    ],
    "thresholds": {"yes": 0.85, "no": 0.15},
    "decide": [
        {"when": "!('baja.pide' in p)", "then": "doubt"},
        {"when": "p['baja.pide'] >= th['yes']", "then": True},
        {"when": "p['baja.pide'] <= th['no']", "then": False},
        {"otherwise": "doubt"},
    ],
    "floor": {"builtin": "rule_or_jev"},
    "same": {"builtin": "bool_eq"},
    "examples": [
        {"answers": {"baja.pide": 0.9}, "expect": True},
        {"answers": {"baja.pide": 0.1}, "expect": False},
        {"answers": {"baja.pide": 0.5}, "expect": "doubt"},
        {"answers": {}, "expect": "doubt"},
    ],
}


def _write(root: Path, capabilities: dict[str, dict[str, Any]], *, bundle: dict[str, Any] | None = None,
           catalog: dict[str, Any] | None = None) -> tuple[Path, Path]:
    bundle_dir = root / "tienda-ventas"
    (bundle_dir / "capabilities").mkdir(parents=True)
    head = {"id": "tienda-ventas", "version": 1, "engine_contract": 1, "oracle": "jev-1.13",
            "capabilities": list(capabilities)}
    (bundle_dir / "bundle.yaml").write_text(yaml.safe_dump({**head, **(bundle or {})}, allow_unicode=True), encoding="utf-8")
    for name, spec in capabilities.items():
        (bundle_dir / "capabilities" / f"{name}.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    catalog_path = root / "builtins.yaml"
    catalog_path.write_text(yaml.safe_dump(catalog or CATALOG), encoding="utf-8")
    return bundle_dir, catalog_path


def _codes(tmp_path: Path, capabilities: dict[str, dict[str, Any]], **kw: Any) -> list[str]:
    bundle_dir, catalog_path = _write(tmp_path, capabilities, **kw)
    return [d.code for d in check_bundle(bundle_dir, catalog_path)]


def _baja(**changes: Any) -> dict[str, Any]:
    return {**BAJA, **changes}


def test_a_valid_bundle_passes_and_decides(tmp_path: Path) -> None:
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA})

    assert check_bundle(bundle_dir, catalog_path) == []
    bundle = load_bundle(bundle_dir, catalog_path)
    baja = bundle.capability("baja")
    assert (bundle.id, bundle.version) == ("tienda-ventas", 1)
    assert baja.decide(answers={"baja.pide": 0.9}) is True
    assert baja.decide(answers={"baja.pide": 0.15}) is False
    assert baja.decide(answers={"baja.pide": 0.5}) is DOUBT
    assert baja.decide(answers={}) is DOUBT


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"thresholds": {"yes": 0.85, "no": 0.15}, "threshold": 0.9}, "DB001"),   # llave inventada
        ({"value": "boolean"}, "DB001"),                                            # tipo que no existe
        ({"questions": [{**BAJA["questions"][0], "kind": "yesno"}]}, "DB001"),    # clase de pregunta inventada
        ({"rule": {"builtin": "is_opt_out"}}, "DB004"),                             # builtin que no existe
        ({"rule": {"builtin": "jev"}}, "DB004"),                                    # un piso usado como regla
        ({"state": {"builtin": "customer_message"}}, "DB004"),                      # falta el parámetro del builtin
        ({"decide": [{"when": "p['baja.pid'] >= th['yes']", "then": True}, {"otherwise": "doubt"}]}, "DB005"),
        ({"decide": [{"when": "p['baja.pide'] >= th['si']", "then": True}, {"otherwise": "doubt"}]}, "DB005"),
        ({"decide": [{"when": "p[qid] >= 0.5", "then": True}, {"otherwise": "doubt"}]}, "DB005"),
        ({"decide": [{"when": "prob['baja.pide'] >= 0.5", "then": True}, {"otherwise": "doubt"}]}, "DB006"),
        ({"decide": [{"when": "p['baja.pide'] >= 'alto'", "then": True}, {"otherwise": "doubt"}]}, "DB006"),
        ({"decide": [{"when": "p['baja.pide']", "then": True}, {"otherwise": "doubt"}]}, "DB006"),
        ({"decide": [{"when": "'baja.pide' in p", "then": "si"}, {"otherwise": "doubt"}]}, "DB007"),
        ({"decide": [{"when": "'baja.pide' in p", "then": True}]}, "DB008"),
        ({"decide": [{"otherwise": "doubt"}, {"when": "'baja.pide' in p", "then": True}]}, "DB008"),
        ({"thresholds": {"yes": 85, "no": 0.15}}, "DB009"),
        ({"examples": [{"answers": {"baja.pide": 0.9}, "expect": False}]}, "DB010"),
        ({"examples": [{"answers": {"baja.pid": 0.9}, "expect": True}]}, "DB010"),
        ({"decide": [{"when": "choice['baja.pide'] == 'si'", "then": True}, {"otherwise": "doubt"}]}, "DB011"),
        ({"floor": {"builtin": "jev"}}, "DB012"),                                   # se quitó el piso legal
    ],
)
def test_each_mistake_is_rejected_with_its_code(tmp_path: Path, changes: dict[str, Any], code: str) -> None:
    assert code in _codes(tmp_path, {"baja": _baja(**changes)})


def test_the_bundle_and_its_files_must_match(tmp_path: Path) -> None:
    assert "DB003" in _codes(tmp_path, {"baja": BAJA}, bundle={"capabilities": ["baja", "cortesia"]})
    assert "DB003" in _codes(tmp_path / "b", {"baja": _baja(capability="bajas")})


def test_the_bundle_id_is_its_folder(tmp_path: Path) -> None:
    """La configuración de la tienda nombra la carpeta: si el id dice otra
    cosa, la traza mentiría sobre qué paquete decidió."""
    assert "DB013" in _codes(tmp_path, {"baja": BAJA}, bundle={"id": "otra-tienda"})


def test_an_engine_contract_the_engine_cannot_run_is_rejected(tmp_path: Path) -> None:
    assert "DB002" in _codes(tmp_path, {"baja": BAJA}, bundle={"engine_contract": 2})


def test_a_repeated_question_id_is_rejected(tmp_path: Path) -> None:
    assert "DB005" in _codes(tmp_path, {"baja": _baja(questions=BAJA["questions"] * 2)})


def test_errors_say_where(tmp_path: Path) -> None:
    bundle_dir, catalog_path = _write(
        tmp_path, {"baja": _baja(decide=[{"when": "p['baja.pid'] >= th['yes']", "then": True}, {"otherwise": "doubt"}])}
    )

    [diagnostic] = check_bundle(bundle_dir, catalog_path)

    assert diagnostic.code == "DB005"
    assert diagnostic.where == "capabilities/baja.yaml: decide[0].when"
    assert "baja.pid" in diagnostic.message


def test_loading_a_broken_bundle_fails_loudly(tmp_path: Path) -> None:
    from src.platform.decisions import BundleError

    bundle_dir, catalog_path = _write(tmp_path, {"baja": _baja(rule={"builtin": "is_opt_out"})})

    with pytest.raises(BundleError) as exc:
        load_bundle(bundle_dir, catalog_path)
    assert [d.code for d in exc.value.diagnostics] == ["DB004"]


def test_a_choice_question_reads_its_option_and_confidence(tmp_path: Path) -> None:
    zona = {
        "capability": "zona", "value": "string", "options": ["bogota", "nacional"], "input": "Inbound",
        "rule": {"builtin": "constant_false"}, "state": None,
        "questions": [{"id": "zona.cual", "kind": "choice", "text": "¿Dónde?",
                       "criteria": {"bogota": "Bogotá", "nacional": "otra ciudad", "ambiguo": "no se sabe"}}],
        "thresholds": {"choice": 0.85},
        "decide": [
            {"when": "!('zona.cual' in choice) || conf['zona.cual'] < th['choice']", "then": "doubt"},
            {"when": "choice['zona.cual'] == 'ambiguo'", "then": "doubt"},
            {"otherwise": "doubt"},
        ],
        "floor": {"builtin": "jev"}, "same": {"builtin": "bool_eq"},
        "examples": [{"answers": {"zona.cual": {"choice": "bogota", "p": 0.95}}, "expect": "doubt"}],
    }
    zona["decide"].insert(2, {"when": "true", "then": "bogota"})
    catalog = {**CATALOG, "builtins": {**CATALOG["builtins"], "constant_false": {"kind": "rule"},
                                       "bool_eq": {"kind": "same"}}}
    zona["examples"] = [
        {"answers": {"zona.cual": {"choice": "bogota", "p": 0.95}}, "expect": "bogota"},
        {"answers": {"zona.cual": {"choice": "bogota", "p": 0.5}}, "expect": "doubt"},
        {"answers": {"zona.cual": {"choice": "ambiguo", "p": 0.95}}, "expect": "doubt"},
    ]
    bundle_dir, catalog_path = _write(tmp_path, {"zona": zona}, catalog=catalog)

    assert check_bundle(bundle_dir, catalog_path) == []
    assert "DB007" in _codes(tmp_path / "x", {"zona": {**zona, "decide": [{"otherwise": "lima"}]}}, catalog=catalog)
