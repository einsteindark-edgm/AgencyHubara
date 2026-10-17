"""Registro de la entrada durable (ADR 2026-10-15) en el worker `sales`.

Gotcha #1 / L-3: la API arranca `InboundMessageWorkflow` por NOMBRE en una
cola; si el worker de esa cola no registra el workflow o una de sus activities,
nada falla al arrancar — el primer mensaje real se queda esperando en Temporal.
Estas pruebas atan el nombre, la cola y el registro.
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.plugins.chats.agent.sales.workflows import inbound_message
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow
from src.plugins.chats.agent.sales.workflows.inbound_session import InboundSessionWorkflow
from src.sdk import get_task_queue, get_worker_spec

_WORKER = Path(__file__).resolve().parents[4] / "src/plugins/chats/workers/sales.py"


def _activity_name(fn: object) -> str:
    return fn.__temporal_activity_definition.name  # type: ignore[attr-defined]


def _worker_kwargs() -> dict[str, ast.expr]:
    calls = [
        node for node in ast.walk(ast.parse(_WORKER.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Worker"
    ]
    return {kw.arg: kw.value for call in calls for kw in call.keywords if kw.arg}


def _starred(expr: ast.expr) -> set[str]:
    """`[*A, *B]` → {"A", "B"}."""
    assert isinstance(expr, ast.List), ast.dump(expr)
    return {e.value.id for e in expr.elts if isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)}


def test_the_sales_worker_registers_the_inbound_workflow_next_to_the_sessions() -> None:
    import src.plugins.chats.workers.sales as sales_worker

    assert sales_worker.INBOUND_WORKFLOWS == [InboundMessageWorkflow, InboundSessionWorkflow]
    assert _starred(_worker_kwargs()["workflows"]) == {"SALES_WORKFLOWS", "INBOUND_WORKFLOWS"}


def test_every_activity_the_inbound_workflow_schedules_is_registered() -> None:
    import src.plugins.chats.workers.sales as sales_worker

    tree = ast.parse(Path(inbound_message.__file__).read_text(encoding="utf-8"))
    scheduled = {
        _activity_name(getattr(inbound_message, node.args[0].id))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_activity"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    registered = {_activity_name(a) for a in sales_worker.INBOUND_ACTIVITIES}

    assert scheduled == {
        # Fase 1 (runs vivos al desplegar la fase 2).
        "ingest_inbound_message",
        "wait_inbound_predecessor",
        # Fase 2: un paso del ingest por activity, el anuncio y la espera.
        "register_inbound_arrival",
        "read_inbound_state",
        "prepare_inbound_turn",
        "write_inbound_history",
        "deliver_inbound_turn",
        "describe_inbound_image",
        "transcribe_inbound_audio",
        "notify_inbound_photo_done",
        "wait_inbound_workflow",
    }
    assert scheduled <= registered, scheduled - registered
    assert _starred(_worker_kwargs()["activities"]) == {"SALES_ACTIVITIES", "INBOUND_ACTIVITIES"}


def test_the_api_starts_the_workflow_by_its_name_on_the_queue_the_worker_listens_to(monkeypatch) -> None:
    import src.plugins.chats.agent.sales.composition as comp
    from src.plugins.chats.agent.sales.use_cases.inbound_entry import INBOUND_WORKFLOW

    monkeypatch.setattr(comp, "_INBOUND_ENTRY", None)
    entry = comp.build_inbound_entry()

    assert INBOUND_WORKFLOW == InboundMessageWorkflow.__temporal_workflow_definition.name  # type: ignore[attr-defined]
    assert entry._task_queue == get_task_queue("chats", "sales")


def test_the_manifest_declares_the_inbound_workflows_on_the_sales_worker() -> None:
    classes = get_worker_spec("chats", "sales")["workflow_classes"]

    assert {"InboundMessageWorkflow", "InboundSessionWorkflow"} <= set(classes)


def test_the_dead_transcription_activity_is_gone_from_the_sales_worker() -> None:
    """Fase 2: la nota de voz se transcribe en `transcribe_inbound_audio`; la
    vieja `transcribe_audio_activity` no la agendaba ningún workflow."""
    import src.plugins.chats.workers.sales as sales_worker

    assert "transcribe_audio_activity" not in {_activity_name(a) for a in sales_worker.SALES_ACTIVITIES}
