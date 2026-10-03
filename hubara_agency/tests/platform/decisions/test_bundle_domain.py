"""El dominio de la tienda (PAQUETES_DE_DECISION.md F5).

Lo que es de UNA tienda y no de la inteligencia —su nombre, la despedida
aprobada, los ejemplos que el agente ve en sus herramientas— va en el
`domain.yaml` del paquete. El catálogo declara qué campos tiene un dominio
y de qué tipo (lo que el código del motor y del agente leen); el
certificador exige que el paquete los traiga todos, del tipo, y nada más.
Las condiciones lo leen como `dom.campo`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.decisions import BundleError, check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import BAJA, CATALOG, _write

CATALOG5: dict[str, Any] = {
    **CATALOG,
    "builtins": {**CATALOG["builtins"], "constant_empty": {"kind": "rule"}, "same_any": {"kind": "same"},
                 "jev_any": {"kind": "floor"}},
    "domain": {
        "store_name": "string",
        "farewell": "string",
        "vocabulary": {"variant_examples": "string", "hook_examples": "list<string>"},
    },
}
DOMAIN: dict[str, Any] = {
    "store_name": "Velas del Sol",
    "farewell": "Listo, tu pedido quedó registrado. Gracias por elegir a Velas del Sol.",
    "vocabulary": {"variant_examples": "'lavanda', 'el morado'", "hook_examples": ["¡Hola de nuevo!"]},
}
DESPEDIDA: dict[str, Any] = {
    **BAJA,
    "capability": "despedida",
    "value": "string",
    "rule": {"builtin": "constant_empty"},
    "decide": [
        {"when": "!('baja.pide' in p)", "then": "doubt"},
        {"otherwise": {"expr": "dom.farewell"}},
    ],
    "floor": {"builtin": "jev_any"},
    "same": {"builtin": "same_any"},
    "examples": [
        {"answers": {"baja.pide": 0.9}, "expect": "Listo, tu pedido quedó registrado. Gracias por elegir a Velas del Sol."},
        {"answers": {}, "expect": "doubt"},
    ],
}


def _bundle(tmp_path: Path, domain: dict[str, Any] | None, *, capability: dict[str, Any] = DESPEDIDA) -> tuple[Path, Path]:
    bundle_dir, catalog_path = _write(tmp_path, {capability["capability"]: capability}, catalog=CATALOG5)
    if domain is not None:
        (bundle_dir / "domain.yaml").write_text(yaml.safe_dump(domain, allow_unicode=True), encoding="utf-8")
    return bundle_dir, catalog_path


def _codes(tmp_path: Path, domain: dict[str, Any] | None, **kw: Any) -> list[str]:
    return [str(d) for d in check_bundle(*_bundle(tmp_path, domain, **kw))]


def test_the_domain_is_read_by_the_engine_and_the_code(tmp_path: Path) -> None:
    from src.platform.decisions import load_domain

    bundle_dir, catalog_path = _bundle(tmp_path, DOMAIN)

    bundle = load_bundle(bundle_dir, catalog_path)

    assert bundle.domain == DOMAIN
    assert load_domain(bundle_dir, catalog_path) == DOMAIN
    assert bundle.capability("despedida").decide(answers={"baja.pide": 0.9}) == DOMAIN["farewell"]


@pytest.mark.parametrize(
    ("domain", "where"),
    [
        (None, "domain.yaml"),                                                            # falta el archivo
        ({k: v for k, v in DOMAIN.items() if k != "farewell"}, "farewell"),                # falta un campo
        ({**DOMAIN, "telefono": "x"}, "telefono"),                                         # sobra un campo
        ({**DOMAIN, "store_name": 3}, "store_name"),                                       # de otro tipo
        ({**DOMAIN, "vocabulary": {"variant_examples": "x"}}, "vocabulary.hook_examples"),  # falta en una sección
        ({**DOMAIN, "vocabulary": {**DOMAIN["vocabulary"], "hook_examples": "uno"}}, "vocabulary.hook_examples"),
    ],
)
def test_the_domain_must_be_the_declared_one(tmp_path: Path, domain: dict[str, Any] | None, where: str) -> None:
    codes = _codes(tmp_path, domain)

    assert any(c.startswith("DB014") and where in c for c in codes), codes


def test_a_condition_reads_only_declared_domain_fields(tmp_path: Path) -> None:
    rows = [{"when": "!('baja.pide' in p)", "then": "doubt"}, {"otherwise": {"expr": "dom.despedida"}}]

    codes = _codes(tmp_path, DOMAIN, capability={**DESPEDIDA, "decide": rows})

    assert any(c.startswith("DB005") and "despedida" in c for c in codes), codes


def test_load_domain_fails_loudly(tmp_path: Path) -> None:
    from src.platform.decisions import load_domain

    bundle_dir, catalog_path = _bundle(tmp_path, {**DOMAIN, "store_name": 3})

    with pytest.raises(BundleError):
        load_domain(bundle_dir, catalog_path)


def test_a_catalog_without_domain_needs_no_domain_file(tmp_path: Path) -> None:
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA})

    assert check_bundle(bundle_dir, catalog_path) == []
    assert load_bundle(bundle_dir, catalog_path).domain == {}
