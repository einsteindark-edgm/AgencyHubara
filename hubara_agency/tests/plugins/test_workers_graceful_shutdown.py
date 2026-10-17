"""Todo worker de Temporal se apaga en orden (auditoría de Temporal, 2026-10-15).

Ningún worker manejaba SIGTERM: un deploy los mataba a mitad de sus activities
(un envío de WhatsApp, un turno del LLM) y Temporal se enteraba al vencer sus
timeouts. Cada worker corre con `run_worker(worker)` (SIGTERM → deja de pedir
tareas y espera a las que están en curso) y le pasa a su `Worker(...)` el
`graceful_shutdown_timeout` común. Un worker nuevo que entra con
un `worker.run()` pelado (suelto o dentro de un `gather`) lo frena este guard.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PLUGINS = Path(__file__).resolve().parents[2] / "src" / "plugins"
#: El laboratorio corre UNA corrida (`client.execute_workflow`) dentro de
#: `async with Worker(...)` y termina: no es un worker de larga vida.
_ONE_SHOT = {"chats/workers/sales_lab.py"}


def _worker_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Worker"
    ]


def _worker_modules() -> list[Path]:
    """Los módulos que construyen un `Worker(...)` (no los que lo nombran en un docstring)."""
    return sorted(
        p
        for p in _PLUGINS.glob("*/workers/*.py")
        if p.relative_to(_PLUGINS).as_posix() not in _ONE_SHOT
        and _worker_calls(ast.parse(p.read_text(encoding="utf-8")))
    )


def test_the_guard_sees_every_long_lived_worker() -> None:
    names = {p.relative_to(_PLUGINS).as_posix() for p in _worker_modules()}
    assert {"chats/workers/sales.py", "eta/workers/eta.py", "marketing/workers/campaigns.py"} <= names
    assert len(names) >= 10, names


@pytest.mark.parametrize("module", _worker_modules(), ids=lambda p: p.relative_to(_PLUGINS).as_posix())
def test_every_worker_shuts_down_in_order(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = _worker_calls(tree)
    for call in calls:
        kwargs = {k.arg: k.value for k in call.keywords}
        value = kwargs.get("graceful_shutdown_timeout")
        assert isinstance(value, ast.Name) and value.id == "WORKER_GRACEFUL_SHUTDOWN", (
            "el Worker(...) no pasa graceful_shutdown_timeout=WORKER_GRACEFUL_SHUTDOWN"
        )
    assert _bare_runs(tree) == [], "corre un worker sin manejar SIGTERM: usá `await run_worker(worker)`"
    assert "run_worker(" in source, "no corre el worker con `run_worker` (SIGTERM → apagado ordenado)"


def _bare_runs(tree: ast.AST) -> list[int]:
    """Las líneas con un `.run()` sin argumentos (`worker.run()`, también dentro
    de un `asyncio.gather(...)`); `asyncio.run(main())` lleva argumento."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and not node.args
        and not node.keywords
    ]


def test_the_guard_catches_workers_run_inside_a_gather() -> None:
    """Caso negativo (premortem 2026-10-16): un `asyncio.gather(*(w.run() ...))`
    no dice `await worker.run()`, pero tampoco maneja SIGTERM."""
    source = (
        "import asyncio\n"
        "async def main():\n"
        "    await asyncio.gather(*(worker.run() for worker in build_workers()))\n"
        "asyncio.run(main())\n"
    )
    assert _bare_runs(ast.parse(source)) == [3]
