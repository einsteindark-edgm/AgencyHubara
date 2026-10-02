"""El dominio de la tienda en el paquete (PAQUETES_DE_DECISION.md F5).

Los ejemplos de la tienda que el agente ve en sus herramientas y en el
gancho de remarketing («'lavanda', 'el morado'», «el Velón de Cristo»,
«nuestras velas religiosas») salen del `domain.yaml` del paquete activo, no
del código. Para llevar el agente a otra tienda (Vincenzo, zapatos) se
escribe otro dominio; forge ya no reemplaza texto en el código del agente.

Pasarlos al paquete no cambia ni un carácter de lo que ve el LLM en la
tienda actual: la foto congelada (`fixtures/store_domain/`) lo exige.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.plugins.chats.shared import store_pack
from tests.plugins.chats.store_texts import TOOL_MODULES, agent_texts

FIXTURE = Path(__file__).parents[2] / "fixtures" / "store_domain" / "ventas_agent_texts.json"
SRC = Path(__file__).parents[3]
PROMPT_MODULES = (*TOOL_MODULES, "src.plugins.chats.agent.remarketing.prompts")
#: Ejemplos que son de ESTA tienda (velas, sus productos, sus aromas).
STORE_EXAMPLES = (
    "lavanda", "el morado", "verde menta", "40 horas", "cera de palma", "luz serena", "plato: leo",
    "velas religiosas", "velas artesanales", "velón de cristo", "cruz de vida", "estos aromas",
    "aromas / colores", "por aroma/color", "elegir aroma/color", "opciones de aroma/color",
)


@pytest.fixture(autouse=True)
def _default_store(monkeypatch):
    monkeypatch.delenv(store_pack.BUNDLE_ENV, raising=False)
    store_pack.reset()
    yield
    store_pack.reset()


def _code_strings(module: str) -> list[tuple[int, str]]:
    """Las cadenas del código (sin docstrings ni comentarios): lo que llega al LLM."""
    tree = ast.parse((SRC / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_what_the_agent_reads_for_the_current_store_is_unchanged() -> None:
    assert agent_texts() == json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("module", PROMPT_MODULES)
def test_the_store_examples_live_in_the_store_pack_not_in_the_code(module: str) -> None:
    found = [
        (line, example) for line, text in _code_strings(module) for example in STORE_EXAMPLES if example in text.lower()
    ]

    assert found == [], f"{module}: ejemplos de la tienda en el código (van en domain.yaml: vocabulary): {found}"


def test_the_store_domain_is_certified_and_feeds_the_agent() -> None:
    domain = store_pack.store_domain()
    texts = json.dumps(agent_texts(), ensure_ascii=False)

    assert domain["store_name"] == "Hubara"
    for key, value in domain["vocabulary"].items():
        for piece in value if isinstance(value, list) else [value]:
            assert piece in texts, f"vocabulary.{key} no llega a lo que lee el agente: {piece!r}"


def test_the_farewell_is_the_one_the_v1_workflow_sends() -> None:
    from src.plugins.chats.agent.sales.decisions.egress import ORDER_REGISTERED_FALLBACK_FAREWELL

    assert store_pack.store_domain()["farewell_order_registered"] == ORDER_REGISTERED_FALLBACK_FAREWELL


def test_a_store_that_does_not_exist_fails_loudly(monkeypatch) -> None:
    from src.sdk.decisionkit import BundleError

    monkeypatch.setenv(store_pack.BUNDLE_ENV, "tienda-que-no-existe")

    with pytest.raises(BundleError, match="DB013"):
        store_pack.store_domain()
