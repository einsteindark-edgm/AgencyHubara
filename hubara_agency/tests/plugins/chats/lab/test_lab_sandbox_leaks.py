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
from pathlib import Path

from tests.plugins.chats.lab.test_lab_sandbox_materialize import SID, WS, _bench, _case

_PROBE = Path(__file__).parent / "probes" / "sandbox_turn_probe.py"
_HUBARA = Path(__file__).resolve().parents[4]
_LOCAL = {"127.0.0.1", "localhost", "::1"}
_FORBIDDEN_ENV = ("WHATSAPP_", "MEDUSA_", "META_", "TEMPORAL_API_KEY", "TEMPORAL_ADDRESS", "COGNITO_", "PAYMENT_")


def _run_probe(tmp_path: Path, case: dict, *, all_tools: bool = False) -> dict:
    bench = _bench(tmp_path)
    (bench / "promotions.json").write_text("[]", encoding="utf-8")
    sandbox = tmp_path / "lab" / "runs" / "run-test" / "A1" / "0" / "case-1"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    report_path = tmp_path / "report.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(_FORBIDDEN_ENV)}
    env["PYTHONPATH"] = str(_HUBARA)
    env["HUBARA_ENV"] = "lab"
    env["ENABLED_PLUGINS"] = "chats"
    if all_tools:
        env["PROBE_ALL_TOOLS"] = "1"
    proc = subprocess.run(
        [sys.executable, str(_PROBE), str(case_path), str(bench), str(sandbox), str(report_path)],
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
