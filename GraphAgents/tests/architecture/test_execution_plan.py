"""El **plan de ejecución** del explorer — la proyección del ORDEN en que corre un
supervisor, derivada de la MISMA semántica que compone el `loader`
(`build_supervisor_graph` / el threading del LocalRuntime) pero sin langgraph.

Es el golden estructural del "flujo": para cada strategy, qué pasos corren, en qué
orden, con qué binding `$state.<x>` y con qué rol (cadena / fan-out+join / dispatch).
Si el loader cambia el orden de composición, este golden lo caza.

Además: el badge de catálogo `group` (demo/variant) que el explorer pinta sobre las
islas que no son el pod de producción.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sdk.graph import build_graph, execution_plan
from sdk.manifest_model import EXT_KEYS, TaskGraphManifest, load_manifest, native_subset

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents
MANIFESTS = ROOT / "manifests"


def _plan(name: str) -> dict:
    m = load_manifest(MANIFESTS / name)
    return execution_plan(m, ROOT)


# --- sequential: el pod real, 6 pasos EN ORDEN con sus bindings -------------------------

EXPECTED_ADS_ANALYTICS = [
    {"order": 1, "agent": "ctwa-insights", "archetype": "extractor",
     "inputs": {"payload": "$state.meta_insights"}, "role": "step"},
    {"order": 2, "agent": "sales-ledger", "archetype": "extractor",
     "inputs": {"payload": "$state.manual_sales"}, "role": "step"},
    {"order": 3, "agent": "ctwa-campaign-funnel", "archetype": "analyzer",
     "inputs": {"entities_payload": "$state.entities_payload",
                "insights_payload": "$state.meta_insights"}, "role": "step"},
    {"order": 4, "agent": "blended-economics", "archetype": "analyzer",
     "inputs": {"currency": "$state.currency", "insights": "$state.insights",
                "sales": "$state.sales"}, "role": "step"},
    {"order": 5, "agent": "numbers-qa", "archetype": "analyzer",
     "inputs": {"days": "$state.days", "period": "$state.period"}, "role": "step"},
    {"order": 6, "agent": "ctwa-report", "archetype": "reporter",
     "inputs": {"days": "$state.days", "period": "$state.period",
                "unmatched": "$state.unmatched", "qa_passed": "$state.passed",
                "campaigns": "$state.campaigns"}, "role": "step"},
]


def test_sequential_plan_is_the_six_steps_in_order():
    plan = _plan("ads-analytics.taskgraph.yaml")
    assert plan["agent"] == "ads-analytics"
    assert plan["strategy"] == "sequential"
    assert plan["steps"] == EXPECTED_ADS_ANALYTICS


# --- router: UN dispatch; el 1er agente es el default, los demás branches --------------

def test_router_plan_marks_default_and_branches():
    plan = _plan("ads-supervisor.taskgraph.yaml")
    assert plan["strategy"] == "router"
    assert plan["steps"] == [
        {"order": 1, "agent": "meta-insights", "archetype": "extractor",
         "inputs": {}, "role": "default_branch"},
        {"order": 1, "agent": "roas-cac", "archetype": "analyzer",
         "inputs": {}, "role": "branch"},
    ]


# --- parallel: fan-out concurrente (mismo order) + un join terminal --------------------

def test_parallel_plan_fans_out_then_joins():
    plan = _plan("ads-extractors-parallel.taskgraph.yaml")
    assert plan["strategy"] == "parallel"
    assert plan["steps"] == [
        {"order": 1, "agent": "ctwa-insights", "archetype": "extractor",
         "inputs": {"payload": "$state.meta_insights"}, "role": "fan_out"},
        {"order": 1, "agent": "sales-ledger", "archetype": "extractor",
         "inputs": {"payload": "$state.manual_sales"}, "role": "fan_out"},
        {"order": 2, "agent": None, "archetype": None, "inputs": {}, "role": "join"},
    ]


# --- leaf (capability): un solo paso, él mismo ----------------------------------------

def test_leaf_agent_plan_is_a_single_step():
    m = load_manifest(MANIFESTS / "greeter.agent.yaml")
    plan = execution_plan(m, ROOT)
    assert plan["steps"] == [
        {"order": 1, "agent": "greeter", "archetype": "reporter", "inputs": {}, "role": "single"},
    ]


# --- group badge: las islas demo/variant lo declaran; el pod no -----------------------

def test_demo_and_variant_agents_carry_group_badge():
    g = build_graph(ROOT)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["agent:greeter"]["group"] == "demo"
    assert by["agent:ads-supervisor"]["group"] == "demo"
    assert by["agent:ads-extractors-parallel"]["group"] == "variant"
    # los de producción NO llevan badge (ausente = pod real)
    assert by["agent:ads-analytics"]["group"] is None
    assert by["agent:ctwa-insights"]["group"] is None


def test_group_rejects_unknown_value():
    """El check del campo (regla de oro): un `group` fuera de {demo,variant} no carga."""
    with pytest.raises(ValidationError):
        TaskGraphManifest.model_validate(
            {"name": "x", "archetype": "reporter",
             "capability": "graphs.greeter:build", "group": "bogus"}
        )


def _all_keys(d: dict) -> set:
    keys = set(d)
    for a in d.get("agents", []):
        keys |= _all_keys(a)
    return keys


def test_native_subset_strips_every_ext_key():
    """Higiene de deploy (regla de oro, la pata del check de stripping): NINGUNA llave
    NUESTRA (EXT_KEYS — incl. `group`) sobrevive a `native_subset` en ningún nivel del
    árbol → el YAML que ve `agentspan deploy` es solo el nativo. Guard contra el drift
    entre EXT_KEYS y la allow-list de native_subset."""
    for p in sorted(MANIFESTS.glob("*.yaml")):
        leaked = _all_keys(native_subset(load_manifest(p))) & EXT_KEYS
        assert not leaked, f"{p.name}: native_subset filtró ext-keys {sorted(leaked)}"
