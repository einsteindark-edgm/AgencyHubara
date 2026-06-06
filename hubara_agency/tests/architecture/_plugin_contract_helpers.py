"""Helpers para los tests del PLUGIN_CONTRACT.md (aislamiento + toggle de plugins).

Separado de ``conftest.py`` (PROTECTED, scope DEHA R-rules) para mantener este
harness aditivo y extensible sin tocar el conftest. Reusa ``SRC_ROOT`` del
conftest. Ver ``PLUGIN_CONTRACT.md`` (reglas P-#) y ``PLUGIN_ARCHITECTURE_TESTS.md``.

Todo es introspección pura de filesystem + AST — sin importar módulos de plugins
(así no requiere env Medusa ni levanta clientes a nivel módulo).
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

from tests.architecture.conftest import SRC_ROOT  # hubara_agency/src

REPO_ROOT: Path = SRC_ROOT.parents[1]                       # raíz del repo (sobre hubara_agency/)
HUBARA_ROOT: Path = SRC_ROOT.parent                         # hubara_agency/
BE_PLUGINS: Path = SRC_ROOT / "plugins"                     # hubara_agency/src/plugins
PLATFORM: Path = SRC_ROOT / "platform"
FE_PLUGINS: Path = REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


def _is_plugin_dir(p: Path) -> bool:
    """Dir de plugin real (excluye `_schema`, dotfiles)."""
    return p.is_dir() and not p.name.startswith((".", "_"))


def manifest_ids() -> list[str]:
    """Ids de plugins con manifest (lado frontend, la SSoT de manifests)."""
    if not FE_PLUGINS.exists():
        return []
    return sorted(
        d.name
        for d in FE_PLUGINS.iterdir()
        if _is_plugin_dir(d) and (d / "plugin.yaml").exists()
    )


def all_manifests() -> list[tuple[str, dict]]:
    """[(plugin_id, manifest_dict)] ordenado por id."""
    out: list[tuple[str, dict]] = []
    if not FE_PLUGINS.exists():
        return out
    for d in sorted(FE_PLUGINS.iterdir()):
        if not _is_plugin_dir(d):
            continue
        mf = d / "plugin.yaml"
        if not mf.exists():
            continue
        data = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            out.append((d.name, data))
    return out


def backend_plugin_ids() -> list[str]:
    """Dirs de plugin bajo src/plugins/ (lado backend)."""
    if not BE_PLUGINS.exists():
        return []
    return sorted(d.name for d in BE_PLUGINS.iterdir() if _is_plugin_dir(d))


def backend_has_code(plugin_id: str) -> bool:
    """True si src/plugins/<id>/ tiene .py reales (no solo __init__.py)."""
    d = BE_PLUGINS / plugin_id
    if not d.is_dir():
        return False
    return any(p.suffix == ".py" and p.name != "__init__.py" for p in d.rglob("*.py"))


def real_imports(pyfile: Path) -> set[str]:
    """Módulos importados por AST — NO docstrings ni comentarios.

    (Evita el falso positivo del ejemplo `from src.plugins...` que vive en el
    docstring de `platform/orchestration/dispatcher.py`.)
    """
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    return mods


def declared_modules(manifest: dict) -> list[str]:
    """Todos los módulos Python que el manifest referencia (api + agent)."""
    api = manifest.get("api") or {}
    agent = manifest.get("agent") or {}
    mods: list[str] = []
    if isinstance(api.get("python_module"), str):
        mods.append(api["python_module"])
    for r in api.get("legacy_routers") or []:
        if isinstance(r, dict) and isinstance(r.get("module"), str):
            mods.append(r["module"])
    if isinstance(agent.get("python_module"), str):
        mods.append(agent["python_module"])
    for w in agent.get("workers") or []:
        if isinstance(w, dict) and isinstance(w.get("module"), str):
            mods.append(w["module"])
    return mods


def iter_py(root: Path):
    """.py bajo `root`, salteando __pycache__."""
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
