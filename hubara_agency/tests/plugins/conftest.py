"""Fixtures locales para ``tests/plugins/``.

``ephemeral_module`` resuelve un problema concreto de los tests del loader:
varios tests necesitan importar módulos Python "fake" para verificar el
comportamiento del bootstrap (e.g. un router con/sin ``router`` attribute,
un módulo con shape incorrecto, etc.). Estos módulos deben ser **importables
por path absoluto** (``tests.plugins._fake_X``), lo que descarta ``tmp_path``
(no está en ``sys.path`` por defecto en este árbol).

La fixture crea el archivo en ``tests/plugins/`` y se asegura de borrarlo
**siempre** (incluso si el test crashea con SIGKILL/AssertionError en mitad),
porque pytest invoca el finalizer en `addfinalizer`/`yield` no importa qué.
También invalida ``sys.modules`` para evitar que un test posterior reciba un
módulo cacheado de una iteración anterior.

Pre-premortem PR9 los tests hacían ``try/finally`` manual — bug-prone (un
test olvidaba el finally y dejaba ``_fake_X.py`` huérfano en el árbol de
tests, contaminando corridas futuras).
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def ephemeral_module(request: pytest.FixtureRequest) -> Callable[[str, str], str]:
    """Crea un módulo Python en ``tests/plugins/`` que se borra al teardown.

    Devuelve una factory ``create(name, body) -> dotted_path``:

        def test_something(ephemeral_module):
            dotted = ephemeral_module(
                "_fake_router",
                "from fastapi import APIRouter\\nrouter = APIRouter()\\n",
            )
            # dotted == "tests.plugins._fake_router"
            # archivo creado en tests/plugins/_fake_router.py
            # se borra automáticamente al final del test, hasta si crashea.

    El cleanup también purga ``sys.modules`` para que el próximo test que
    pida el mismo nombre obtenga un módulo fresco (no cacheado).
    """
    plugins_dir = Path(__file__).parent
    created: list[tuple[Path, str]] = []

    def _create(name: str, body: str) -> str:
        assert name.startswith("_"), (
            f"ephemeral_module name must start with underscore (got {name!r}) — "
            "convención para que pytest no lo recoja como test."
        )
        path = plugins_dir / f"{name}.py"
        path.write_text(body, encoding="utf-8")
        dotted = f"tests.plugins.{name}"
        created.append((path, dotted))
        return dotted

    def _cleanup() -> None:
        for path, dotted in created:
            path.unlink(missing_ok=True)
            sys.modules.pop(dotted, None)

    request.addfinalizer(_cleanup)
    return _create
