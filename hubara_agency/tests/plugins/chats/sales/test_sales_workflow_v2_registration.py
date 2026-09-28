"""Registro del workflow de ventas V2 (motor de decisiones F4, diseño v2 §08).

El V2 se registra en el MISMO worker que el V1, al lado: el registro de bots
arranca uno u otro por nombre. El V1 sigue primero en el manifiesto
(`get_workflow_name` devuelve el índice 0: quien no consulta el enrutador
arranca el de hoy). La activity nueva del egreso (`decide_egress`) está en la
lista del worker, y cada activity que el V2 agenda está registrada (L-3: una
que falte no rompe el arranque, rompe la primera conversación real).
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.plugins.chats.agent.sales.workflows import sales_session_v2
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from src.sdk import get_worker_spec, get_workflow_name
from src.sdk.agentkit import CONVERSATIONAL_TURN_ACTIVITIES

_WORKER = Path(__file__).resolve().parents[4] / "src/plugins/chats/workers/sales.py"


def _activity_name(fn: object) -> str:
    return fn.__temporal_activity_definition.name  # type: ignore[attr-defined]


def test_the_sales_worker_registers_v1_first_and_v2_next_to_it() -> None:
    import src.plugins.chats.workers.sales as sales_worker

    assert getattr(sales_worker, "SALES_WORKFLOWS", None) == [HubaraSalesSessionWorkflow, HubaraSalesSessionWorkflowV2]
    # `main()` registra esa lista (y no una clase suelta).
    calls = [
        node for node in ast.walk(ast.parse(_WORKER.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Worker"
    ]
    workflows = {kw.arg: kw.value for call in calls for kw in call.keywords}.get("workflows")
    assert isinstance(workflows, ast.Name) and workflows.id == "SALES_WORKFLOWS"


def test_the_manifest_declares_v2_after_v1() -> None:
    assert get_worker_spec("chats", "sales")["workflow_classes"] == [
        "HubaraSalesSessionWorkflow",
        "HubaraSalesSessionWorkflowV2",
    ]
    assert get_workflow_name("chats", "sales") == "HubaraSalesSessionWorkflow"


def test_the_sales_worker_registers_the_egress_activity() -> None:
    import src.plugins.chats.workers.sales as sales_worker

    assert "decide_egress" in {_activity_name(a) for a in sales_worker.SALES_ACTIVITIES}


def _scheduled_by_v2() -> set[str]:
    """Nombres de las activities que el V2 agenda (`execute_activity` /
    `start_activity` con una activity importada como primer argumento)."""
    tree = ast.parse(Path(sales_session_v2.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute_activity", "start_activity")
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            names.add(_activity_name(getattr(sales_session_v2, node.args[0].id)))
    return names


def test_every_activity_v2_schedules_is_registered_in_the_sales_worker() -> None:
    import src.plugins.chats.workers.sales as sales_worker

    registered = {_activity_name(a) for a in sales_worker.SALES_ACTIVITIES}
    scheduled = _scheduled_by_v2() | {_activity_name(a) for a in CONVERSATIONAL_TURN_ACTIVITIES}

    assert "decide_egress" in scheduled and "perceive_burst" in scheduled
    assert scheduled <= registered, scheduled - registered
