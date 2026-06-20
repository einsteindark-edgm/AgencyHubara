"""G-BIND binding (caso NEGATIVO primero): el `with:` de una tool `uses:` debe nombrar
SOLO inputs DECLARADOS en el contrato de esa tool (`tool.yaml` `inputs`). Un `with:` que
nombra claves que el contrato no tiene es documentación que MIENTE sobre el contrato:
inerte (el loader inyecta la impl, no aplica el binding) pero engañosa para un lector.

Regla de oro del plugin-protocol: *ningún campo del manifest sin su check*. El `with:`
(modelado como `ToolSpec.binding`) no tenía check → este lo agrega.
"""
from __future__ import annotations

from pathlib import Path

from sdk.manifest_model import TaskGraphManifest
from sdk.testkit.checks import run_checks

GA = Path(__file__).resolve().parents[2]


def _agent_with_tool_binding(binding: dict) -> TaskGraphManifest:
    # complement-funnel (catálogo real) declara inputs: {payload}. El `with:` se valida
    # contra ese contrato.
    return TaskGraphManifest.model_validate({
        "name": "t-bind", "archetype": "analyzer",
        "tools": [{"uses": "complement-funnel@1", "with": binding}],
    })


def test_with_que_contradice_el_contrato_de_la_tool_no_certifica() -> None:
    node = _agent_with_tool_binding({"entities": "$state.entities", "insights": "$state.insights"})
    errs = run_checks(node, GA)["errors"]
    assert any("G-BIND" in e and "complement-funnel" in e and "contrato" in e for e in errs), (
        f"esperaba un error G-BIND de binding↔contrato; hubo: {errs}"
    )


def test_with_que_matchea_el_contrato_pasa() -> None:
    # payload SÍ es un input declarado de complement-funnel → el binding no miente.
    node = _agent_with_tool_binding({"payload": "$state.x"})
    errs = run_checks(node, GA)["errors"]
    assert not any("G-BIND" in e and "contrato" in e for e in errs), f"no esperaba error; hubo: {errs}"


def test_sin_with_no_hay_nada_que_validar() -> None:
    # una tool sin `with:` (la capability la cablea por dentro) no dispara el check.
    node = TaskGraphManifest.model_validate({
        "name": "t-bind", "archetype": "analyzer",
        "tools": [{"uses": "complement-funnel@1"}],
    })
    errs = run_checks(node, GA)["errors"]
    assert not any("G-BIND" in e and "contrato" in e for e in errs), f"no esperaba error; hubo: {errs}"
