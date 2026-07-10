"""El CLI `status` — el POLL del progreso de un run durable.

Imprime a stdout el workflow JSON CRUDO de Conductor (con `tasks[]`) de un execution-id; lo corre
el buzón de hubara por SSM en la caja (`python -m sdk.cli status <eid> --runtime agentspan`) y lo
`interpret`a del lado hubara — el buzón NUNCA se conecta directo a Conductor, solo lee este stdout.
Unit: mockea `trace.fetch_workflow` (no necesita el server `:6767`).
"""
from __future__ import annotations

import argparse
import json

import sdk.cli as cli


def test_cmd_status_imprime_el_workflow_json_crudo_de_conductor(monkeypatch, capsys) -> None:
    wf = {
        "workflowId": "w1",
        "status": "RUNNING",
        "tasks": [{"taskType": "HUMAN", "status": "IN_PROGRESS", "inputData": {"context": {"q": "?"}}}],
    }
    seen: dict = {}

    def fake_fetch(execution_id, server_url=None, timeout=8):
        seen["eid"] = execution_id
        seen["server_url"] = server_url  # None → la caja consulta su Conductor LOCAL (localhost:6767)
        return wf

    monkeypatch.setattr("sdk.trace.fetch_workflow", fake_fetch)
    args = argparse.Namespace(execution_id="exec-9", runtime="agentspan")
    rc = cli.cmd_status(args)

    assert rc == 0
    assert seen["eid"] == "exec-9"
    assert seen["server_url"] is None  # NO se le pasa una URL remota: la caja usa su propio Conductor
    out = json.loads(capsys.readouterr().out)  # stdout = el workflow JSON crudo, parseable por el buzón
    assert out == wf


def test_cmd_status_exige_runtime_agentspan(monkeypatch) -> None:
    # status es SOLO para el poll durable (Conductor); sin --runtime agentspan, no-op rc=1, no toca Conductor.
    called = {"n": 0}
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    args = argparse.Namespace(execution_id="e", runtime="local")
    assert cli.cmd_status(args) == 1
    assert called["n"] == 0


def test_cmd_status_compact_reduce_el_json_para_ssm(monkeypatch, capsys) -> None:
    # Run prod ce80f73f→7efb9fc6 (2026-07-10): SSM get_command_invocation trunca
    # stdout a ~24KB; el workflow JSON crudo de Conductor echoea el snapshot
    # ENTERO dentro de tasks[].inputData/outputData y no cabe → el buzón no
    # parsea. --compact emite SOLO lo que interpret() consume.
    import json

    fat = {
        "workflowId": "wf-1",
        "status": "COMPLETED",
        "reasonForIncompletion": None,
        "output": {"result": "{'dispatch': []}"},
        "input": {"payload": {"x": "y" * 5000}},
        "tasks": [
            {
                "taskType": "window-strategist_ingest",
                "status": "COMPLETED",
                "inputData": {"state": "z" * 9000},
                "outputData": {"state": "z" * 9000},
            },
            {
                "taskType": "HUMAN",
                "status": "IN_PROGRESS",
                "inputData": {"context": {"monto": 5}, "otro": "w" * 5000},
            },
        ],
    }
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda eid: fat)
    args = argparse.Namespace(execution_id="wf-1", runtime="agentspan", compact=True)
    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert len(out) < 2000, f"compact debe caber holgado en SSM: {len(out)}"
    d = json.loads(out)
    assert d["status"] == "COMPLETED"
    assert d["output"] == {"result": "{'dispatch': []}"}
    assert d["tasks"][0] == {
        "taskType": "window-strategist_ingest",
        "status": "COMPLETED",
    }
    assert d["tasks"][1]["inputData"] == {"context": {"monto": 5}}


def test_cmd_status_compact_poda_el_output_gigante_de_un_completed(monkeypatch, capsys) -> None:
    """Run real bd3c2d4e (2026-07-10, primer análisis con datos reales): el
    output.result de un COMPLETED echoea el ESTADO ENTERO del grafo (input de
    Meta incluido) → ~30KB → SSM trunca a 24KB → el buzón no parsea y el poller
    queda CIEGO reintentando en silencio para siempre. --compact debe podar las
    keys más pesadas del result hasta caber, preservando las livianas (el
    reporte/verdict que el operador ve) y declarando lo podado."""
    state = {
        "meta_insights": {"data": ["x" * 100] * 400},  # el eco gigante (~40KB)
        "markdown": "## Hubara — Ads Analytics",
        "verdict": "ok",
    }
    fat = {
        "workflowId": "wf-2",
        "status": "COMPLETED",
        "reasonForIncompletion": None,
        "output": {"result": json.dumps(state)},
        "tasks": [],
    }
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda eid: fat)
    args = argparse.Namespace(execution_id="wf-2", runtime="agentspan", compact=True)

    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert len(out) < 20000, f"debe caber bajo el techo de SSM: {len(out)}"
    d = json.loads(out)
    assert d["status"] == "COMPLETED"
    inner = json.loads(d["output"]["result"])
    assert inner["markdown"] == "## Hubara — Ads Analytics"  # lo liviano sobrevive
    assert inner["verdict"] == "ok"
    assert "meta_insights" not in inner  # el eco gigante se poda
    assert "meta_insights" in inner["_pruned_keys"]  # y queda declarado


def test_cmd_status_compact_poda_ADENTRO_del_acumulador_del_supervisor(monkeypatch, capsys) -> None:
    """Post-#153 en prod: el state de un SUPERVISOR es {"acc": {...todo...}} — UNA
    sola key contenedora. La poda de primer nivel dropeaba `acc` ENTERO y el
    operador recibía `{"_pruned_keys": ["acc"]}` sin reporte ni verdict (feedback
    2026-07-10). La poda debe descender al contenedor y podar SUS keys: los ecos
    gigantes se caen, el reporte/verdict SOBREVIVEN."""
    acc = {
        "meta_insights": {"data": ["x" * 100] * 400},  # eco gigante (~40KB)
        "manual_sales": {"sales": ["y" * 100] * 200},  # otro eco (~20KB)
        "markdown": "## Hubara — Ads Analytics (CTWA)",
        "verdict": "insufficient_data",
        "qa_passed": True,
    }
    fat = {
        "workflowId": "wf-3",
        "status": "COMPLETED",
        "reasonForIncompletion": None,
        "output": {"result": json.dumps({"acc": acc})},
        "tasks": [],
    }
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda eid: fat)
    args = argparse.Namespace(execution_id="wf-3", runtime="agentspan", compact=True)

    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert len(out) < 20000
    inner = json.loads(json.loads(out)["output"]["result"])
    assert "acc" in inner  # el contenedor NO se dropea entero
    assert inner["acc"]["markdown"] == "## Hubara — Ads Analytics (CTWA)"
    assert inner["acc"]["verdict"] == "insufficient_data"
    assert inner["acc"]["qa_passed"] is True
    assert "meta_insights" not in inner["acc"]  # los ecos gigantes sí se podan
    # lo podado queda declarado con su ruta
    assert "acc.meta_insights" in inner["_pruned_keys"]


def test_cmd_status_compact_proyecta_al_contrato_del_ultimo_nodo(monkeypatch, capsys) -> None:
    """Feedback operador 2026-07-10: 'que el agente devuelva el nodo del análisis
    y no hacer poda'. Para un supervisor SECUENCIAL el resultado ES lo que declara
    el contrato del ÚLTIMO nodo (G-CONTRACT: ctwa-report.outputs → markdown /
    verdict / qa_passed / narrative). --compact PROYECTA el estado a esas keys —
    determinista, garantizado, sin heurística de tamaño. La poda queda de
    fallback para workflows sin manifest resoluble."""
    acc = {
        "meta_insights": {"data": ["x" * 100] * 400},  # eco gigante
        "campaigns": [{"id": 1}, {"id": 2}],           # intermedio (no es del reporter)
        "markdown": "## Hubara — Ads Analytics",
        "verdict": "ok",
        "qa_passed": True,
    }
    fat = {
        "workflowId": "wf-4",
        "workflowName": "ads-analytics",  # el manifest REAL del repo
        "status": "COMPLETED",
        "reasonForIncompletion": None,
        "output": {"result": json.dumps({"acc": acc})},
        "tasks": [],
    }
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda eid: fat)
    args = argparse.Namespace(execution_id="wf-4", runtime="agentspan", compact=True)

    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert len(out) < 20000
    inner = json.loads(json.loads(out)["output"]["result"])
    # EXACTAMENTE el contrato del reporter — plano, sin wrapper acc ni intermedios
    assert inner["markdown"] == "## Hubara — Ads Analytics"
    assert inner["verdict"] == "ok"
    assert inner["qa_passed"] is True
    assert "meta_insights" not in inner
    assert "campaigns" not in inner
    assert "acc" not in inner
    assert inner["_projected_from"] == "ctwa-report"


def test_cmd_status_compact_proyeccion_resuelve_por_prefijo_de_task(monkeypatch, capsys) -> None:
    """Si Conductor no expone workflowName, el graph se resuelve por el PREFIJO de
    los taskTypes (`ads-analytics_ctwa_insights_0` → manifest ads-analytics)."""
    acc = {"meta_insights": {"data": ["x" * 100] * 300}, "markdown": "## R", "verdict": "ok"}
    fat = {
        "workflowId": "wf-5",
        "status": "COMPLETED",
        "reasonForIncompletion": None,
        "output": {"result": json.dumps({"acc": acc})},
        "tasks": [{"taskType": "ads-analytics_ctwa_insights_0", "status": "COMPLETED"}],
    }
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda eid: fat)
    args = argparse.Namespace(execution_id="wf-5", runtime="agentspan", compact=True)

    assert cli.cmd_status(args) == 0
    inner = json.loads(json.loads(capsys.readouterr().out)["output"]["result"])
    assert inner["markdown"] == "## R"
    assert inner["_projected_from"] == "ctwa-report"
    assert "meta_insights" not in inner
