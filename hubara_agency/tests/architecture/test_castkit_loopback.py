"""Gate — ningún plugin abre httpx loopback a :8000 fuera del castkit (Canal 3).

Incidente 2026-06-23: los casts loopback plugin→plugin (`agents_admin→chats`,
`chats→orders`) hacían httpx directo a la MISMA app (`127.0.0.1:8000`) sin
portar el `Authorization` → 401 en el 2º hop bajo `require_auth`, re-envuelto en
`detail` doble-anidado. El fix centralizó el cast en `src.sdk.castkit.forward`
(porta la identidad + semántica honesta de fallos + `detail` desanidado).

Este gate impide que la clase de bug vuelva: un plugin que abra un cliente httpx
apuntando a loopback :8000 (en vez de delegar en castkit) FALLA el merge — es la
3ª pata de la regla de oro (el símbolo nuevo del SDK nace con su check).

Connectors a vendors EXTERNOS (Medusa, Meta) siguen usando httpx libremente: el
gate solo caza el patrón de CAST loopback (cliente httpx + literal :8000). El
propio `castkit` vive en `src/sdk/` (fuera de `plugins/`) y es el único
autorizado para el self-call.
"""
from __future__ import annotations

import ast

from tests.architecture.conftest import SRC_ROOT, parse_file, relative_to_hubara

_PLUGINS_ROOT = SRC_ROOT / "plugins"
_LOOPBACK_LITERALS = ("127.0.0.1:8000", "localhost:8000")


def _opens_loopback_http(tree: ast.Module) -> bool:
    """True si el módulo ABRE un cliente httpx Y referencia loopback :8000.

    AST-based: ignora comentarios/docstrings — un literal en código cuenta, en un
    comentario no. Distingue cast-loopback de connector-externo: un connector a
    Medusa/Meta abre httpx pero NO apunta a :8000; un cast migrado referencia
    :8000 (su `base_url`) pero ya NO abre httpx (delega en castkit).
    """
    opens_httpx_client = False
    has_loopback_literal = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"AsyncClient", "Client"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
        ):
            opens_httpx_client = True
        # Import aliased: `from httpx import AsyncClient` evade el prefijo `httpx.`.
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "httpx"
            and any(a.name in {"AsyncClient", "Client"} for a in node.names)
        ):
            opens_httpx_client = True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(lit in node.value for lit in _LOOPBACK_LITERALS):
                has_loopback_literal = True
    return opens_httpx_client and has_loopback_literal


def _iter_plugin_py_files() -> list:
    return sorted(p for p in _PLUGINS_ROOT.rglob("*.py") if p.is_file())


# --- caso NEGATIVO primero: el gate CAZA el estado roto ----------------------


def test_detector_catches_unguarded_loopback_cast() -> None:
    """Un plugin que abre httpx a :8000 directo (el bug que tuvimos) se detecta."""
    broken = (
        "import httpx\n"
        "async def bad(req):\n"
        "    async with httpx.AsyncClient() as c:\n"
        "        return await c.get('http://127.0.0.1:8000/api/chats/evals/history')\n"
    )
    assert _opens_loopback_http(ast.parse(broken)) is True


def test_detector_catches_aliased_httpx_import() -> None:
    """Endurecimiento: `from httpx import AsyncClient` (sin prefijo) también se caza."""
    aliased = (
        "from httpx import AsyncClient\n"
        "async def bad(req):\n"
        "    async with AsyncClient() as c:\n"
        "        return await c.get('http://127.0.0.1:8000/api/x')\n"
    )
    assert _opens_loopback_http(ast.parse(aliased)) is True


def test_detector_ignores_external_connector() -> None:
    """httpx a un vendor externo (sin loopback) NO se marca — connectors son OK."""
    ok = (
        "import httpx\n"
        "async def fine():\n"
        "    async with httpx.AsyncClient(base_url='https://graph.facebook.com') as c:\n"
        "        return await c.get('/me')\n"
    )
    assert _opens_loopback_http(ast.parse(ok)) is False


def test_detector_ignores_migrated_cast() -> None:
    """Un cast migrado (literal :8000 como base, SIN abrir httpx) NO se marca."""
    migrated = (
        "import os\n"
        "from src.sdk import castkit\n"
        "def _base():\n"
        "    return os.environ.get('CHATS_API_BASE', 'http://127.0.0.1:8000')\n"
        "async def fwd(req):\n"
        "    return await castkit.forward(req, 'GET', '/x', base_url=_base(),\n"
        "                                 timeout=15.0, cast_label='a->b')\n"
    )
    assert _opens_loopback_http(ast.parse(migrated)) is False


# --- el árbol real: cero ofensores (todos los casts van por castkit) ---------


def test_no_plugin_opens_loopback_http_outside_castkit() -> None:
    """Ningún archivo de plugin abre httpx loopback :8000 — usá src.sdk.castkit."""
    offenders = [
        relative_to_hubara(path)
        for path in _iter_plugin_py_files()
        if _opens_loopback_http(parse_file(path))
    ]
    assert not offenders, (
        "Cast loopback sin guardar — reemplazá el httpx directo por "
        "`src.sdk.castkit.forward` (porta Authorization + semántica honesta):\n  "
        + "\n  ".join(offenders)
    )
