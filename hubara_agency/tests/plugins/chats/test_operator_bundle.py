"""El paquete de decisión de la App Operador (`operador`): lo que Jev decide en la app.

Dos capacidades sobre el motor genérico (`src.sdk.decisionkit`), con su propio
catálogo (como el `centinela` del Order Sentinel): `burbuja` (qué acción del
chat va primero) e `incendio` (gravedad, tipo y si empeora). Las reglas de hoy
(`chats/shared/mobile_rules.py`) arman las jugadas legales y los incendios y son
el respaldo cuando Jev duda o no responde.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

PACKAGE = "src.plugins.chats.shared.operator.decisions"


def _decisions() -> Any:
    assert importlib.util.find_spec(PACKAGE) is not None, "falta el paquete de la app (chats/shared/operator/decisions)"
    return importlib.import_module(PACKAGE)


@pytest.fixture(autouse=True)
def _fresh_bundle():
    yield
    if importlib.util.find_spec(PACKAGE) is not None:
        importlib.import_module(PACKAGE).reset()


def test_the_operator_bundle_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle

    decisions = _decisions()

    assert check_bundle(decisions.BUNDLES_DIR / decisions.BUNDLE_ID, decisions.CATALOG_PATH) == []
    bundle = decisions.active_bundle()
    assert (bundle.ref, sorted(bundle.capabilities)) == ("operador-2@2", ["burbuja", "incendio"])
    assert bundle.oracle == "jev-1.13"


def test_the_certifier_finds_it_with_the_others() -> None:
    from src.sdk.cli.decisions import discover_bundles

    decisions = _decisions()
    repo = Path(__file__).resolve().parents[4]

    assert decisions.BUNDLES_DIR / decisions.BUNDLE_ID in discover_bundles(repo)


def test_the_catalog_and_the_code_declare_the_same_builtins() -> None:
    decisions = _decisions()
    catalog = yaml.safe_load(decisions.CATALOG_PATH.read_text(encoding="utf-8"))

    assert {name: spec["kind"] for name, spec in catalog["builtins"].items()} == {
        name: kind for name, (kind, _fn) in decisions.BUILTINS.items()
    }


def test_each_capability_says_in_plain_words_what_it_solves() -> None:
    decisions = _decisions()
    catalog = yaml.safe_load(decisions.CATALOG_PATH.read_text(encoding="utf-8"))

    assert set(catalog["about"]) == {"burbuja", "incendio"}
    assert all(about["solves"].strip() for about in catalog["about"].values())
