"""El TRACE de runtime — `build_trace` anota el `execution_plan` con el estado VIVO de
cada nodo, leído de una ejecución de Conductor. PURO (sin red): golden contra un
fixture real (trimmeado) de `/api/workflow/{id}?includeTasks=true` + casos sintéticos
de los estados que el explorer pinta (done/running/pending/failed) y del join parallel.

El mapeo task→paso es por el naming que emite el loader (`<wf>_<nodo>_<i>`): si el loader
renombra los nodos, este golden lo caza (igual que test_execution_plan para el orden).
"""
from __future__ import annotations

import json
from pathlib import Path

from sdk.graph import execution_plan
from sdk.manifest_model import load_manifest
from sdk.trace import build_trace

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents
MANIFESTS = ROOT / "manifests"
FIXTURES = ROOT / "fixtures"


def _plan(name: str) -> dict:
    return execution_plan(load_manifest(MANIFESTS / name), ROOT)


# --- golden: ejecución REAL (trimmeada) del pod, los 6 nodos COMPLETED -----------------

def test_trace_annotates_real_sequential_run_in_order():
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = json.loads((FIXTURES / "conductor_workflow_ads_analytics.json").read_text(encoding="utf-8"))
    trace = build_trace(plan, wf)

    assert trace["execution_id"] == wf["workflowId"]
    assert trace["workflow_status"] == "COMPLETED"
    assert trace["strategy"] == "sequential"
    assert [s["agent"] for s in trace["steps"]] == [
        "ctwa-insights", "sales-ledger", "ctwa-campaign-funnel",
        "blended-economics", "numbers-qa", "ctwa-report",
    ]
    assert all(s["runtime"]["status"] == "done" for s in trace["steps"])
    assert all(s["runtime"]["retries"] == 0 for s in trace["steps"])
    assert all(s["runtime"]["task_id"] for s in trace["steps"])  # cada paso linkea su task
    assert trace["unmatched_tasks"] == []


# --- estados: done(+retries+ms) · running · pending (sin task todavía) -----------------

def test_trace_maps_states_and_marks_steps_without_task_pending():
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-x", "status": "RUNNING",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 2, "startTime": 1000, "endTime": 1500, "taskId": "t0"},
            {"referenceTaskName": "ads-analytics_sales_ledger_1", "status": "IN_PROGRESS",
             "retryCount": 0, "startTime": 1500, "endTime": 0, "taskId": "t1"},
        ],
    }
    trace = build_trace(plan, wf)
    st = trace["steps"]
    assert st[0]["runtime"] == {"status": "done", "retries": 2, "ms": 500, "task_id": "t0"}
    assert st[1]["runtime"]["status"] == "running"
    assert st[1]["runtime"]["ms"] is None  # sin endTime válido
    assert st[2]["runtime"]["status"] == "pending"  # ctwa-campaign-funnel: sin task aún
    assert st[2]["runtime"]["task_id"] is None
    assert trace["workflow_status"] == "RUNNING"


# --- parallel: el join terminal tiene su propio task y matchea -------------------------

def test_trace_matches_parallel_fanout_and_join():
    plan = _plan("ads-extractors-parallel.taskgraph.yaml")
    wf = {
        "workflowId": "wf-p", "status": "COMPLETED",
        "tasks": [
            {"referenceTaskName": "ads-extractors-parallel_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 0, "startTime": 10, "endTime": 25, "taskId": "a"},
            {"referenceTaskName": "ads-extractors-parallel_sales_ledger_1", "status": "COMPLETED",
             "retryCount": 0, "startTime": 10, "endTime": 22, "taskId": "b"},
            {"referenceTaskName": "ads-extractors-parallel_join", "status": "COMPLETED",
             "retryCount": 0, "startTime": 25, "endTime": 30, "taskId": "j"},
        ],
    }
    trace = build_trace(plan, wf)
    by_role = {}
    for s in trace["steps"]:
        by_role.setdefault(s["role"], []).append(s)
    assert [s["runtime"]["task_id"] for s in by_role["fan_out"]] == ["a", "b"]
    assert by_role["join"][0]["runtime"]["task_id"] == "j"
    assert by_role["join"][0]["runtime"]["ms"] == 5
    assert trace["unmatched_tasks"] == []


# --- failed: un nodo que crasheó se marca failed con sus retries -----------------------

def test_trace_join_binds_the_loader_node_not_the_framework_join_task():
    """Conductor emite system-tasks `_fork`/`_join` (FORK_JOIN) ADEMÁS del nodo `<wf>_join`
    del loader. El match anclado al prefijo del workflow debe bindear el nodo REAL del loader
    (el que foldea los patches, L-14), no la system-task de fan-in del framework."""
    plan = _plan("ads-extractors-parallel.taskgraph.yaml")
    wf = {
        "workflowId": "p", "workflowName": "ads-extractors-parallel", "status": "COMPLETED",
        "tasks": [
            {"referenceTaskName": "_fork", "taskType": "FORK", "status": "COMPLETED",
             "retryCount": 0, "startTime": 1, "endTime": 1, "taskId": "fork"},
            {"referenceTaskName": "ads-extractors-parallel_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 0, "startTime": 2, "endTime": 5, "taskId": "a"},
            {"referenceTaskName": "ads-extractors-parallel_sales_ledger_1", "status": "COMPLETED",
             "retryCount": 0, "startTime": 2, "endTime": 4, "taskId": "b"},
            {"referenceTaskName": "_join", "taskType": "JOIN", "status": "COMPLETED",
             "retryCount": 0, "startTime": 5, "endTime": 6, "taskId": "sysjoin"},
            {"referenceTaskName": "ads-extractors-parallel_join", "status": "COMPLETED",
             "retryCount": 0, "startTime": 6, "endTime": 9, "taskId": "realjoin"},
        ],
    }
    trace = build_trace(plan, wf)
    join_step = [s for s in trace["steps"] if s["role"] == "join"][0]
    assert join_step["runtime"]["task_id"] == "realjoin"  # NO "sysjoin"
    assert join_step["runtime"]["ms"] == 3
    # las system-tasks de Conductor (no son nodos del usuario) quedan en unmatched
    refs = [t["referenceTaskName"] for t in trace["unmatched_tasks"]]
    assert "_fork" in refs and "_join" in refs


def test_trace_retried_node_reports_terminal_attempt_not_first():
    """Conductor devuelve UN task POR INTENTO (mismo referenceTaskName). El trace reporta el
    intento FINAL (recovery), no el primero: un nodo que falló-y-se-recuperó es `done`/retries=1,
    no `failed`/retries=0. Es la razón de ser del runtime durable — reportar el fallo sería mentir."""
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "r", "workflowName": "ads-analytics", "status": "COMPLETED",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 0, "startTime": 1, "endTime": 2, "taskId": "ok"},
            {"referenceTaskName": "ads-analytics_sales_ledger_1", "status": "FAILED",
             "retryCount": 0, "startTime": 2, "endTime": 3, "taskId": "try0"},
            {"referenceTaskName": "ads-analytics_sales_ledger_1", "status": "COMPLETED",
             "retryCount": 1, "startTime": 3, "endTime": 5, "taskId": "try1"},
        ],
    }
    trace = build_trace(plan, wf)
    st1 = trace["steps"][1]["runtime"]
    assert st1["status"] == "done"   # el resultado terminal, NO el 1er intento fallido
    assert st1["retries"] == 1
    assert st1["task_id"] == "try1"
    assert trace["unmatched_tasks"] == []  # el intento fallido es el mismo nodo, no ensucia


def test_trace_marks_failed_node():
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-f", "status": "FAILED",
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_insights_0", "status": "COMPLETED",
             "retryCount": 0, "startTime": 1, "endTime": 2, "taskId": "ok"},
            {"referenceTaskName": "ads-analytics_sales_ledger_1", "status": "FAILED",
             "retryCount": 3, "startTime": 2, "endTime": 9, "taskId": "boom"},
        ],
    }
    trace = build_trace(plan, wf)
    assert trace["steps"][1]["runtime"] == {"status": "failed", "retries": 3, "ms": 7, "task_id": "boom"}
