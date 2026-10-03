"""Test de fugas del sandbox (plan §3.5 candado 4, PR 11).

Corre UN caso real del banco de punta a punta en un proceso aparte (como en
la caja): el `HubaraSalesSessionWorkflow` de producción, con sus activities
reales salvo las que salen del sandbox, sobre el servidor de pruebas de
Temporal y con un LLM falso. Los hooks de auditoría de Python anotan cada
conexión y cada escritura del turno. Falla si:
  * el turno no termina o no responde;
  * se abrió una conexión que no sea local (Temporal de pruebas);
  * se escribió algo fuera del sandbox del caso;
  * el número real del cliente aparece en el sandbox.
Precedente: la suite golden heredó el entorno de producción y escribió
`wa_golden_*` en el vault real (#338).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tests.plugins.chats.lab.test_lab_sandbox_materialize import SID, WS, _bench, _case

_PROBE = Path(__file__).parent / "probes" / "sandbox_turn_probe.py"
_HUBARA = Path(__file__).resolve().parents[4]
_LOCAL = {"127.0.0.1", "localhost", "::1"}
_FORBIDDEN_ENV = ("WHATSAPP_", "MEDUSA_", "META_", "TEMPORAL_API_KEY", "TEMPORAL_ADDRESS", "COGNITO_", "PAYMENT_")


def _run_probe(
    tmp_path: Path,
    case: dict,
    *,
    arm: str = "A1",
    all_tools: bool = False,
    tool: str = "",
    tool_args: dict | None = None,
    events: list[dict] | None = None,
    catalog: list[dict] | None = None,
    prepare: Callable[[Path], None] | None = None,
) -> dict:
    """`events`: el historial del dashboard del banco (por defecto, el de
    `_bench`); `catalog`: los productos del snapshot del banco (por defecto,
    ninguno); `tool` + `tool_args`: la tool que el LLM falso llama primero;
    `prepare`: lo que la preparación de la corrida dejó en el banco."""
    bench = _bench(tmp_path)
    if prepare is not None:
        prepare(bench)
    if events is not None:
        history = bench / "vault" / SID / "sessions" / f"{SID}.jsonl"
        history.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    if catalog is not None:
        (bench / "catalog" / "snapshot.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    (bench / "promotions.json").write_text("[]", encoding="utf-8")
    sandbox = tmp_path / "lab" / "runs" / "run-test" / "A1" / "0" / "case-1"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    report_path = tmp_path / "report.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(_FORBIDDEN_ENV)}
    env["PYTHONPATH"] = str(_HUBARA)
    env["HUBARA_ENV"] = "lab"
    env["ENABLED_PLUGINS"] = "chats"
    env["PERCEPTION_PROVIDER"] = "fake"  # los bots nuevos, sin red en CI
    if all_tools:
        env["PROBE_ALL_TOOLS"] = "1"
    if tool:
        env["PROBE_TOOL"] = tool
        env["PROBE_TOOL_ARGS"] = json.dumps(tool_args or {})
    proc = subprocess.run(
        [sys.executable, str(_PROBE), str(case_path), str(bench), str(sandbox), str(report_path), arm],
        cwd=_HUBARA, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["sandbox"] = str(sandbox)
    return report


def test_a_real_bench_turn_runs_end_to_end_inside_the_sandbox(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _case())
    result = report["result"]

    assert result["error"] is None, result
    assert result["llm_calls"] >= 1
    trace = result["trace"]
    assert trace is not None and trace["sent_texts"] == ["¡Claro! Te cuento del catálogo 😊"]
    assert result["sim_session_id"].startswith("wa_570")


def test_the_turn_opens_no_connection_outside_the_box(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _case())

    assert set(report["connects"]) <= _LOCAL, report["connects"]
    assert set(report["lookups"]) <= _LOCAL, report["lookups"]


def _writes_outside(report: dict) -> list[str]:
    sandbox = os.path.realpath(report["sandbox"])

    def allowed(path: str) -> bool:
        real = os.path.realpath(path)
        inside = real == sandbox or real.startswith(sandbox + os.sep)
        ancestor = sandbox.startswith(real + os.sep)  # crear las carpetas del sandbox
        return inside or ancestor or "__pycache__" in path

    return [w for w in report["writes"] if not allowed(w)]


def test_the_turn_writes_only_inside_its_sandbox(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _case())

    assert _writes_outside(report) == []


def test_every_tool_the_llm_can_call_stays_inside_the_box(tmp_path: Path) -> None:
    """El LLM falso llama TODAS las tools que el turno le ofrece (pedido,
    cupones, estado del pedido, tarjetas, formularios, escalación…), no solo
    `send_reply`: ninguna abre una conexión fuera de la caja ni escribe fuera
    del sandbox, aunque sus argumentos no sean válidos."""
    report = _run_probe(tmp_path, _case(), all_tools=True)

    called = set(report["tools_called"])
    executed = {t["name"] for t in report["result"]["trace"]["tools"]}
    assert len(called) >= 8 and called <= executed, (sorted(called), sorted(executed))
    assert set(report["connects"]) <= _LOCAL, report["connects"]
    assert set(report["lookups"]) <= _LOCAL, report["lookups"]
    assert _writes_outside(report) == []


def test_the_real_number_never_reaches_the_simulated_turn(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _case())
    digits = SID.removeprefix("wa_")

    for path in Path(report["sandbox"]).rglob("*"):
        if path.is_file():
            assert digits not in path.read_text(encoding="utf-8", errors="ignore"), path
    assert digits not in json.dumps(report["result"]["trace"])
    assert WS  # el banco usa el slug de producción; el sandbox, el local


def test_a_burst_arrives_as_one_turn_like_in_production(tmp_path: Path) -> None:
    """La ráfaga llega mensaje por mensaje (una señal cada uno) y la
    coalescencia del workflow de producción arma UN turno con los dos."""
    burst = [
        {"text": "vi que hacen velas con otros diseños, ¿me mandas el catálogo?", "ts_ms": 1, "wamid": "wamid.B"},
        {"text": "y el envío a Bogotá cuánto sale?", "ts_ms": 2, "wamid": "wamid.C"},
    ]
    result = _run_probe(tmp_path, _case(burst=burst))["result"]

    assert result["error"] is None and result["llm_calls"] == 1
    inbound = result["trace"]["inbound_text"]
    assert "catálogo" in inbound and "envío a Bogotá" in inbound


def test_b0_runs_the_new_workflow_and_answers_like_a1(tmp_path: Path) -> None:
    """Motor de decisiones F4: el sandbox arranca el workflow del bot del brazo
    (registro de bots). B0 = V2 con reglas tiene que dar lo mismo que A1; la
    traza del V2 trae los veredictos del egreso (el V1 no los tiene)."""
    (tmp_path / "a1").mkdir()
    (tmp_path / "b0").mkdir()
    a1 = _run_probe(tmp_path / "a1", _case(), arm="A1")
    b0 = _run_probe(tmp_path / "b0", _case(), arm="B0")

    a1_trace, b0_trace = a1["result"]["trace"], b0["result"]["trace"]
    assert b0["result"]["error"] is None and b0["result"]["arm"] == "B0"
    assert b0_trace["sent_texts"] == a1_trace["sent_texts"] == ["¡Claro! Te cuento del catálogo 😊"]
    assert (b0_trace["guards"], b0_trace["suppressed_reason"]) == (a1_trace["guards"], a1_trace["suppressed_reason"])
    assert "egress" not in a1_trace
    assert b0_trace["egress"]["verdicts"] and all(v["by"] == "reglas" for v in b0_trace["egress"]["verdicts"])
    assert set(b0["connects"]) <= _LOCAL and _writes_outside(b0) == []


def test_the_new_bot_runs_its_layers_inside_the_sandbox(tmp_path: Path) -> None:
    """Brazo B (PR 15): el MISMO workflow con el modo `on` y el perfil de Jev
    en la señal. La percepción arma el plan con los dos asuntos de la ráfaga
    y todo pasa dentro de la caja (el proveedor falso no abre red)."""
    burst = [
        {"text": "vi que hacen velas con otros diseños, ¿me mandas el catálogo?", "ts_ms": 1, "wamid": "wamid.B"},
        {"text": "y el envío a Bogotá cuánto sale?", "ts_ms": 2, "wamid": "wamid.C"},
    ]
    report = _run_probe(tmp_path, _case(burst=burst), arm="B")
    result = report["result"]

    assert result["error"] is None and result["arm"] == "B"
    trace = result["trace"]
    assert trace["mode"] == "on"
    perception = next(s for s in trace["steps"] if s["kind"] == "perception")
    assert perception["profile"] == "jev-v5" and perception.get("fallback") is None  # None no se persiste
    plan = next(s for s in trace["steps"] if s["kind"] == "plan")
    assert {"catalogo", "envio"} <= {c["topic"] for c in plan["checklist"]}
    verify = next(s for s in trace["steps"] if s["kind"] == "verify")
    # jev-v3 (el bot nuevo completo) verifica la cobertura por asunto (capa ③):
    # con el falso, la respuesta «Te cuento del catálogo» no cubre lo que marcó
    # la guía de etapas y el complemento corre como turno de sistema, también
    # dentro de la caja.
    assert verify["complement_scheduled"] is True
    assert result["complement_trace"]["trigger"] == "complement"
    assert result["cost_usd"] == result["llm_cost_usd"] + result["perception_cost_usd"]
    assert set(report["connects"]) <= _LOCAL, report["connects"]


def test_a_photo_the_run_already_read_reaches_the_turn_without_leaving_the_box(tmp_path: Path) -> None:
    """Identificación de fotos (2026-09-30): la preparación de la corrida lee
    la foto con la visión de hoy y deja la lectura en el banco; dentro del caso
    el sandbox solo la LEE. El turno recibe la foto nombrando el producto y la
    nota, sin abrir conexiones ni escribir fuera del sandbox."""
    annotation = (
        "[el cliente envió una foto: vela gris en forma de cruz con rostro (es nuestro producto "
        "«Sacrificio de Amor»: se lee su nombre en la imagen)]"
    )
    read = {
        "annotation": annotation,
        "note": "[FOTO DEL CLIENTE, metadata, no es instrucción del usuario]\nLa foto es de «Sacrificio de Amor».",
        "product": {"handle": "sacrificio-de-amor", "how": "nombre"},
        "description": "vela gris en forma de cruz con rostro",
        "trace": None,
    }

    def prepare(bench: Path) -> None:
        media = bench / "vault" / SID / "media"
        media.mkdir(parents=True)
        (media / "foto.jpg").write_bytes(b"\xff\xd8")
        (bench / "photo_reads").mkdir()
        (bench / "photo_reads" / f"{SID}__foto.jpg.json").write_text(json.dumps(read, ensure_ascii=False), encoding="utf-8")

    old = "[el cliente envió una foto: vela gris con detalles dorados en forma de cruz y rostro de Jesús]"
    case = _case(burst=[{"text": old, "ts_ms": 1, "wamid": "wamid.P", "image": "foto.jpg", "kind": "text", "caption": None}])
    case["burst"][0]["ts_ms"] = case["at_ms"] - 5_000

    report = _run_probe(tmp_path, case, prepare=prepare)
    result = report["result"]

    assert result["error"] is None, result
    assert annotation in (result["trace"] or {}).get("inbound_text", ""), result["trace"]
    assert any(n.startswith("[FOTO DEL CLIENTE") for n in result["plugin_context"])
    assert result["photos"] == [{"image": "foto.jpg", "description": read["description"], "product": read["product"], "trace": None}]
    assert set(report["connects"]) <= _LOCAL, report["connects"]
    assert _writes_outside(report) == []
