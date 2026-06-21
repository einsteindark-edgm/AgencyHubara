"""G-WIRE (caso NEGATIVO primero): un supervisor que COMPONE (sequential/parallel/...)
debe declarar `inputs:` (el binding del task graph) en CADA agente, o no certifica. Un
router está exento (rutea a UN agente y le pasa el input). Este check hace que la
ORQUESTACIÓN del task graph sea parte de la certificación — la forma de orquestar en
esta arquitectura es el task graph, así que un supervisor sin wiring es un gate roto.
"""
from __future__ import annotations

from sdk.manifest_model import TaskGraphManifest
from sdk.testkit.checks import run_checks


def _sup(strategy: str, with_inputs: bool) -> TaskGraphManifest:
    ref: dict = {"uses": "agent://x@1"}
    if with_inputs:
        ref["inputs"] = {"payload": "$state.seed"}
    return TaskGraphManifest.model_validate(
        {"name": "t-sup", "archetype": "supervisor", "strategy": strategy, "agents": [ref]}
    )


def test_supervisor_que_compone_sin_inputs_no_certifica() -> None:
    errs = run_checks(_sup("sequential", with_inputs=False))["errors"]
    assert any("G-WIRE" in e for e in errs), f"esperaba un error G-WIRE; hubo: {errs}"


def test_supervisor_que_compone_con_inputs_pasa_gwire() -> None:
    errs = run_checks(_sup("sequential", with_inputs=True))["errors"]
    assert not any("G-WIRE" in e for e in errs)


def test_router_esta_exento_de_gwire() -> None:
    # un router rutea a UN agente y le pasa el input crudo → no necesita wiring.
    errs = run_checks(_sup("router", with_inputs=False))["errors"]
    assert not any("G-WIRE" in e for e in errs)
