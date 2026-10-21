"""`run_api.py` en producción (ADR 2026-10-15, consecuencias): sin `reload`.

Con `reload=True` uvicorn corría un proceso supervisor que vigila archivos y un
hijo `multiprocessing.spawn` que atiende — en la imagen el código no cambia, así
que era memoria y CPU en la caja de 4 GB sin ninguna ganancia. Recargar al
editar (en el host) se pide con `UVICORN_RELOAD=1`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_RUN_API = Path(__file__).resolve().parents[1] / "run_api.py"


def _uvicorn_options(env: dict[str, str]) -> dict:
    spec = importlib.util.spec_from_file_location("run_api_under_test", _RUN_API)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.uvicorn_options(env)


def test_production_runs_without_reload() -> None:
    options = _uvicorn_options({})

    assert options["reload"] is False
    assert (options["host"], options["port"]) == ("0.0.0.0", 8000)


def test_reload_is_asked_for_explicitly() -> None:
    assert _uvicorn_options({"UVICORN_RELOAD": "1"})["reload"] is True
    assert _uvicorn_options({"UVICORN_RELOAD": "0"})["reload"] is False


def test_the_port_comes_from_the_environment() -> None:
    assert _uvicorn_options({"UVICORN_PORT": "8123"})["port"] == 8123
