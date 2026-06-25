"""El estado `awaiting` del trace — observabilidad del pause HITL.

Una HUMAN/WAIT task `IN_PROGRESS` en Conductor es una PAUSA esperando una decisión
humana (el nodo `await_approval` del grafo HITL), NO `running`: el nodo no está
computando, está esperando a un humano. Sin este estado, el pause sería un PUNTO
CIEGO en el explorer/dashboard (caería en `running`/`other`).

PURO (fixture sintético, sin red), mismo patrón que `test_trace.py`.
"""
from __future__ import annotations

from pathlib import Path

from sdk.graph import execution_plan
from sdk.manifest_model import load_manifest
from sdk.trace import build_trace

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents
MANIFESTS = ROOT / "manifests"


def _plan(name: str) -> dict:
    return execution_plan(load_manifest(MANIFESTS / name), ROOT)


def test_human_task_in_progress_se_marca_awaiting() -> None:
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-h", "status": "RUNNING",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 0, "startTime": 1, "endTime": 2, "taskId": "t0"},
            # el nodo HITL: HUMAN task esperando la decisión → awaiting (NO running)
            {"referenceTaskName": "ads-analytics_sales_ledger_1", "taskType": "HUMAN",
             "status": "IN_PROGRESS", "retryCount": 0, "startTime": 2, "endTime": 0, "taskId": "t1"},
        ],
    }
    trace = build_trace(plan, wf)
    assert trace["steps"][1]["runtime"]["status"] == "awaiting"
    assert trace["steps"][0]["runtime"]["status"] == "done"  # el mapeo normal sigue intacto


def test_wait_task_in_progress_tambien_awaiting() -> None:
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-w", "status": "RUNNING",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "taskType": "WAIT",
             "status": "IN_PROGRESS", "retryCount": 0, "startTime": 1, "endTime": 0, "taskId": "w0"},
        ],
    }
    trace = build_trace(plan, wf)
    assert trace["steps"][0]["runtime"]["status"] == "awaiting"


def test_simple_task_in_progress_sigue_running() -> None:
    # GUARD (caso negativo): NO toda IN_PROGRESS es awaiting — solo HUMAN/WAIT.
    # Una task SIMPLE en progreso sigue `running` (si no, el gate sería demasiado amplio).
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-s", "status": "RUNNING",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "taskType": "SIMPLE",
             "status": "IN_PROGRESS", "retryCount": 0, "startTime": 1, "endTime": 0, "taskId": "s0"},
        ],
    }
    trace = build_trace(plan, wf)
    assert trace["steps"][0]["runtime"]["status"] == "running"
