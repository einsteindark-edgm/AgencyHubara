"""El hola mundo corre end-to-end: manifest `greeter` → runnable (resuelve la tool
`hello` del catálogo) → LocalRuntime → saludo. Sin servidor, determinista."""
from __future__ import annotations

from pathlib import Path

from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest
from sdk.runtime import LocalRuntime

GA = Path(__file__).resolve().parents[2]


def test_hola_mundo_corre_via_runtime() -> None:
    m = load_manifest(GA / "manifests" / "greeter.agent.yaml")
    runnable = build_runnable(m, GA)  # el loader inyecta la tool hello del catálogo
    ex = LocalRuntime().run(runnable, {"name": "mundo"})
    assert ex.status == "completed"
    assert ex.output == {"greeting": "hola, mundo"}


def test_hola_mundo_recupera_por_execution_id() -> None:
    m = load_manifest(GA / "manifests" / "greeter.agent.yaml")
    rt = LocalRuntime()
    eid = rt.start_durable(build_runnable(m, GA), {"name": "mundo"})
    assert rt.get(eid).status == "running"
    ex = rt.resume(eid)
    assert ex.id == eid and ex.status == "completed"
    assert ex.output == {"greeting": "hola, mundo"}
