"""Pytest fixtures comunes a toda la suite del refactor DEHA.

`temporal_env` arranca un `WorkflowEnvironment.start_time_skipping` por test que lo
solicite. Es el unico modo seguro de testear workflows sin depender de un cluster
Temporal real (ADR-005).

`_isolate_vault_dir` (autouse, PR8) es defensa en profundidad contra tests que
olvidan mockear `WORKSPACE_VAULT_DIR` o pasar `vault_dir=tmp_path` a los tools
que escriben metadata. Sin este fixture, un test puede contaminar los seeds
commiteados en `hubara_agency/hubara_vault/wa_*/metadata.json`. Ver
PLUGIN_REFACTOR_PLAN.md §8 para la convención del vault.
"""
from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


# ---------------------------------------------------------------------------
# Vault isolation (PR8) — auto-applied a TODA la suite.
# ---------------------------------------------------------------------------
#
# AUTO-DISCOVER (PR11): pre-PR11 esta lista era hardcoded — agregar un módulo
# nuevo que importara `WORKSPACE_VAULT_DIR` requería editarla manualmente
# → conflict de merge cuando 2 PRs (Archon agents) trabajaban en paralelo.
#
# Post-PR11, escaneamos el árbol `src/` via AST y descubrimos automáticamente
# qué módulos hacen `from src.platform.config import WORKSPACE_VAULT_DIR`
# (o `import src.platform.config` + acceso `.WORKSPACE_VAULT_DIR`).
#
# Se ejecuta una sola vez al colectar (cache module-level). Si el scan falla
# por algún motivo, fallback a la lista hardcoded debajo (defense in depth).
import ast


_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Fallback list — usada solo si el AST scan falla. Mantener sincronizada con
# los discovers automáticos NO es necesario; esto es solo defensa.
_VAULT_CAPTURING_MODULES_FALLBACK: tuple[str, ...] = (
    "src.platform.config",
    "src.platform.temporal.dispatcher",
    "src.platform.temporal.activities",
    "src.platform.tools.routing",
    "src.platform.tools.escalation",
    "src.platform.session_history.activities",
    "src.platform.whatsapp.activities",
    "src.platform.registries",
)


def _module_path_to_dotted(path: Path) -> str:
    """`src/foo/bar.py` → `src.foo.bar`. Robusto a `__init__.py`."""
    rel = path.relative_to(_SRC_ROOT.parent)
    parts = rel.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


#: Módulos que EXPONEN `WORKSPACE_VAULT_DIR` como global re-bindeable. Post-SDK
#: (P-28, ADR-2026-06-12) los plugins importan el símbolo desde la fachada
#: `src.sdk.runtime` (que lo re-exporta de `src.platform.config`) en vez de
#: platform directo — ambos orígenes deben descubrirse o el re-bind del fixture
#: se saltea el módulo y el test escribe al vault REAL (caso visto: graphagents).
_VAULT_DIR_SOURCE_MODULES: frozenset[str] = frozenset(
    {"src.platform.config", "src.sdk.runtime"}
)


def _discover_vault_capturing_modules() -> tuple[str, ...]:
    """AST scan: módulos que importan `WORKSPACE_VAULT_DIR` desde un origen sancionado.

    Orígenes (`_VAULT_DIR_SOURCE_MODULES`): `src.platform.config` (el real) y
    `src.sdk.runtime` (la fachada SDK que lo re-exporta — P-28). Detecta:
      1. `from <origen> import WORKSPACE_VAULT_DIR` (más común)
      2. `from <origen> import (..., WORKSPACE_VAULT_DIR, ...)`

    NO detecta `import <origen> as x` + acceso por atributo — pero ese patrón no
    se usa en el repo (verificado al introducir el scan).
    """
    # Siempre incluir los orígenes — otros módulos pueden hacer
    # `import src.platform.config as cfg; cfg.WORKSPACE_VAULT_DIR` y consultar
    # el valor en runtime, no por capture al import.
    found: list[str] = list(_VAULT_DIR_SOURCE_MODULES)
    try:
        for py_file in _SRC_ROOT.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module not in _VAULT_DIR_SOURCE_MODULES:
                    continue
                if any(alias.name == "WORKSPACE_VAULT_DIR" for alias in node.names):
                    found.append(_module_path_to_dotted(py_file))
                    break  # un import basta para este file
        return tuple(sorted(set(found)))
    except Exception:
        # Fallback defensivo — si el scan falla, no rompemos pytest collect.
        return _VAULT_CAPTURING_MODULES_FALLBACK


_VAULT_CAPTURING_MODULES: tuple[str, ...] = _discover_vault_capturing_modules()


@pytest.fixture(autouse=True)
def _isolate_vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Apunta `WORKSPACE_VAULT_DIR` a un tmp por test. Defensa en profundidad.

    Tests que se benefician de saber el path del vault aislado pueden recibir
    este fixture explícitamente y obtener el `Path`:

        def test_x(_isolate_vault_dir: Path):
            assert (_isolate_vault_dir / 'wa_xxx' / 'metadata.json').exists()

    Tests que prefieren DI explícita (pasar `vault_dir=tmp_path` al constructor
    del tool) siguen funcionando — este fixture no estorba.
    """
    isolated = tmp_path / "isolated_vault"
    isolated.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WORKSPACE_VAULT_DIR", str(isolated))

    # Re-bind del module-global en los módulos que ya capturaron el valor por
    # import. monkeypatch.setattr es seguro — restaura al teardown.
    for mod_name in _VAULT_CAPTURING_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue  # módulo aún no migrado / opcional
        if hasattr(mod, "WORKSPACE_VAULT_DIR"):
            monkeypatch.setattr(mod, "WORKSPACE_VAULT_DIR", isolated, raising=False)

    yield isolated
