"""Regla de oro del SDK: `src.sdk.graphagentskit` expone el bridge hubara↔caja
GraphAgents (Launcher port + vendor boto3 + interpret puro de Conductor).

WS-B0 (plan Window Strategist): el bridge nació como código privado del plugin
`ads`; promovido a platform + fachada SDK para que el plugin `reengagement`
(y cualquier otro) lo use sin violar P-3 (imports cross-plugin prohibidos).
"""
from __future__ import annotations


def test_graphagentskit_reexports_platform_bridge():
    import src.platform.graphagents.boto3_launcher as vendor_impl
    import src.platform.graphagents.conductor as conductor_impl
    import src.platform.graphagents.launcher as port_impl
    import src.sdk.graphagentskit as kit

    assert kit.Launcher is port_impl.Launcher
    assert kit.Boto3Launcher is vendor_impl.Boto3Launcher
    assert kit.interpret is conductor_impl.interpret
    assert kit.extract_agent_result is conductor_impl.extract_agent_result


def test_extract_agent_result_desciende_al_contrato_del_agente_directo():
    """PM-001: el output compact de un agente DIRECTO es el STATE COMPLETO del
    grafo ({payload, classified, result}) — el contrato del agente vive un
    nivel adentro. El helper desciende; si la proyección algún día entrega el
    contrato directo, también sirve; sin `dispatch` extraíble → None (el
    workflow lo trata como fallo VISIBLE, nunca 'completed dispatched=0')."""
    from src.sdk.graphagentskit import extract_agent_result

    contract = {"schema_version": 1, "dispatch": [{"session_id": "wa_a"}]}
    full_state = {"payload": {}, "classified": [], "result": contract}

    assert extract_agent_result(full_state) == contract  # desciende
    assert extract_agent_result(contract) == contract  # ya desenvuelto
    assert extract_agent_result({"_pruned_keys": ["result"]}) is None  # podado
    assert extract_agent_result(None) is None
    assert extract_agent_result("crudo-sin-parsear") is None


def test_interpret_completed_unwraps_agentspan_result():
    from src.sdk.graphagentskit import interpret

    wf = {
        "status": "COMPLETED",
        "tasks": [],
        "output": {"result": "{'dispatch': [], 'truncated_by_budget': 0}"},
    }
    state = interpret(wf)
    assert state["status"] == "completed"
    assert state["result"] == {"dispatch": [], "truncated_by_budget": 0}


def test_interpret_completed_unwraps_json_state_with_booleans():
    """PM-001 (premortem order-sentinel): el runtime de la caja documenta el wrap
    como '<json del state final>' (GraphAgents sdk/runtime.py::_unwrap usa
    json.loads) — un state con true/false/null NO es literal Python y
    `ast.literal_eval` lo escupe → interpret devolvía el wrapper crudo y el
    consumer veía cero dispatch con run completed (pérdida silenciosa)."""
    from src.sdk.graphagentskit import interpret

    wf = {
        "status": "COMPLETED",
        "tasks": [],
        "output": {
            "result": '{"payload": {"has_media": true}, "classified": null, '
            '"result": {"dispatch": [], "suppressed": []}}'
        },
    }
    state = interpret(wf)
    assert state["status"] == "completed"
    assert state["result"] == {
        "payload": {"has_media": True},
        "classified": None,
        "result": {"dispatch": [], "suppressed": []},
    }
