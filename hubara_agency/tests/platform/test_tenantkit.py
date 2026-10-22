"""Regla de oro del SDK: `src.sdk.tenantkit` re-exporta el contexto de tienda
(Acktos Stores, F1.1) para los plugins.

El check es por IDENTIDAD (`is`), por pureza (el contexto solo usa la stdlib:
lo van a importar los interceptores de Temporal) y por lector único: nadie
fuera de `src/platform/tenant/` lee `ACKTOS_TENANT` ni `ACKTOS_CELL` del
entorno. Quien lo leyera directo se saltaría el alcance de la tienda y, en la
celda, respondería una tienda que no es.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
_STDLIB_ONLY = {"os", "re", "collections.abc", "contextlib", "contextvars"}


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_tenantkit_reexports_the_store_context() -> None:
    import src.platform.tenant.context as impl
    import src.sdk.tenantkit as kit

    assert kit.current_tenant is impl.current_tenant
    assert kit.tenant_scope is impl.tenant_scope
    assert kit.validate_tenant_id is impl.validate_tenant_id
    assert kit.TenantNotInScope is impl.TenantNotInScope
    assert kit.InvalidTenantId is impl.InvalidTenantId


def test_tenantkit_only_depends_on_the_store_context() -> None:
    assert _imported_modules(_SRC / "sdk" / "tenantkit.py") == {"src.platform.tenant"}


def test_the_store_context_only_uses_the_standard_library() -> None:
    package = _SRC / "platform" / "tenant"
    assert _imported_modules(package / "context.py") <= _STDLIB_ONLY
    assert _imported_modules(package / "__init__.py") == {"src.platform.tenant.context"}


def test_nobody_else_reads_the_store_from_the_environment() -> None:
    reader = re.compile(r"""["'](ACKTOS_TENANT|ACKTOS_CELL)["']""")
    offenders = sorted(
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if "platform/tenant" not in path.as_posix() and reader.search(path.read_text(encoding="utf-8"))
    )

    assert offenders == [], f"leen la tienda del entorno sin pasar por current_tenant(): {offenders}"
