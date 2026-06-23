"""El TRACE de runtime — `build_trace` anota el `execution_plan` con el estado VIVO de
cada nodo, leído de una ejecución de Conductor. PURO (sin red): golden contra un
fixture real (trimmeado) de `/api/workflow/{id}?includeTasks=true` + casos sintéticos
de los estados que el explorer pinta (done/running/pending/failed) y del join parallel.

El mapeo task→paso es por el naming que emite el loader (`<wf>_<nodo>_<i>`): si el loader
renombra los nodos, este golden lo caza (igual que test_execution_plan para el orden).
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from sdk import trace as trace_mod
from sdk.graph import execution_plan
from sdk.manifest_model import load_manifest
from sdk.trace import build_trace, fetch_runs

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


# --- el trace lleva, además del runtime: las tools que compone cada nodo + el seed raíz ---

def test_trace_carries_composed_tools_and_supervisor_root_seed():
    """El trace propaga (a) las `tools` que compone cada nodo — la sub-ejecución que dibuja
    el explorer, disponible ANTES de que el nodo corra (es estática, del manifest) — y (b) el
    `agent` raíz + el `seed` con que arrancó el supervisor (el `input` del workflow desenvuelto
    de `{acc: seed}`, lo que el loader envuelve). Eso alimenta el modal del agente principal."""
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {
        "workflowId": "wf-s", "workflowName": "ads-analytics", "status": "RUNNING",
        "input": {"acc": {"currency": "COP", "days": [{"d": 1}]}},
        "tasks": [
            {"referenceTaskName": "ads-analytics_ctwa_campaign_funnel_2", "status": "COMPLETED",
             "retryCount": 0, "startTime": 1, "endTime": 2, "taskId": "t2"},
        ],
    }
    trace = build_trace(plan, wf)
    assert trace["agent"] == "ads-analytics"
    assert trace["seed"] == {"currency": "COP", "days": [{"d": 1}]}  # desenvuelto de {acc: ...}
    funnel = next(s for s in trace["steps"] if s["agent"] == "ctwa-campaign-funnel")
    # las tools viajan aunque el nodo esté pending o done — son la verdad estática del nodo
    assert funnel["tools"] == ["parse-meta-entities", "meta-ads-insights", "complement-funnel"]
    assert next(s for s in trace["steps"] if s["agent"] == "ctwa-insights")["runtime"]["status"] == "pending"


def test_trace_seed_unwraps_agentspan_prompt_envelope():
    """Caso REAL (visto en vivo): AgentSpan NO persiste el workflow input como `{acc: seed}` sino
    envuelto en `{'prompt': '<repr Python del estado>'}`. El modal del supervisor debe mostrar el
    SEED limpio igual → se pela el `prompt` con `ast.literal_eval` (seguro: solo literales) y se
    desenvuelve el `acc`. Sin esto la pestaña input mostraba `{'prompt': "{'acc': {...}}"}` crudo."""
    plan = _plan("ads-analytics.taskgraph.yaml")
    wf = {"workflowId": "w", "workflowName": "ads-analytics",
          "input": {"prompt": "{'acc': {'currency': 'COP', 'days': [1, 2]}}"}, "tasks": []}
    assert build_trace(plan, wf)["seed"] == {"currency": "COP", "days": [1, 2]}
    # un prompt no parseable NO miente: devuelve el envelope crudo (no inventa un seed)
    bad = {"workflowId": "w", "input": {"prompt": "<no-parseable>"}, "tasks": []}
    assert build_trace(plan, bad)["seed"] == {"prompt": "<no-parseable>"}


def test_trace_seed_falls_back_to_raw_input_then_none():
    """`seed`: una capability hoja manda el input CRUDO (sin envoltorio `acc`) → se devuelve tal
    cual. Sin `input` (fixture trimmeado) → None: el modal del supervisor degrada a 'ver Probar'."""
    plan = _plan("ads-analytics.taskgraph.yaml")
    raw = build_trace(plan, {"workflowId": "w", "input": {"name": "ada"}, "tasks": []})
    assert raw["seed"] == {"name": "ada"}  # no es {acc:...} → crudo
    absent = build_trace(plan, {"workflowId": "w", "tasks": []})
    assert absent["seed"] is None


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


# --- fleet poll: filtrar a RUNNING server-side (la clave de escala) --------------------

def test_fetch_runs_running_only_filters_status_server_side(monkeypatch):
    """El poll de FLOTA debe pedirle a Conductor SOLO las ejecuciones activas
    (`status IN (RUNNING)`) — así una request cubre N concurrentes sin traerse las 245
    históricas. Verifica que el filtro va en el query, server-side."""
    seen = {}

    def fake_get(url, timeout=8):
        seen["url"] = url
        return {"results": [{"workflowId": "x", "workflowType": "ads-analytics",
                             "status": "RUNNING", "startTime": 1}]}

    monkeypatch.setattr(trace_mod, "_get", fake_get)
    runs = fetch_runs(running_only=True, limit=100)
    assert "status IN (RUNNING)" in urllib.parse.unquote_plus(seen["url"])
    assert runs == [{"execution_id": "x", "agent": "ads-analytics", "status": "RUNNING", "startTime": 1}]


def test_fetch_runs_without_filter_does_not_constrain_status(monkeypatch):
    seen = {}
    monkeypatch.setattr(trace_mod, "_get", lambda url, timeout=8: seen.update(url=url) or {"results": []})
    fetch_runs(limit=10)
    assert "status IN" not in urllib.parse.unquote_plus(seen["url"])
