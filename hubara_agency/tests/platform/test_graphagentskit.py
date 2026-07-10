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
