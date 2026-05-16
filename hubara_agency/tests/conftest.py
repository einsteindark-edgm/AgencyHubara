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
# Lista de módulos que capturaron `WORKSPACE_VAULT_DIR` por import (== module
# global). Cualquier módulo nuevo que importe `from src.platform.config import
# WORKSPACE_VAULT_DIR` debe agregarse aquí — o usar `vault_dir=tmp_path` DI
# explícito en los tests del módulo.
_VAULT_CAPTURING_MODULES: tuple[str, ...] = (
    "src.platform.config",
    "src.platform.temporal.dispatcher",
    "src.platform.temporal.activities",
    "src.platform.tools.routing",
    "src.platform.tools.escalation",
    "src.platform.session_history.activities",
    "src.platform.whatsapp.activities",
    "src.platform.registries",
    "src.plugins.chats.agent.sales.tools.tags",
    "src.plugins.chats.agent.sales.composition",
    "src.plugins.chats.agent.sales.activities.bootstrap_session",
    "src.plugins.chats.agent.remarketing.activities.bootstrap_session",
    "src.plugins.chats.api.dashboard",
    "src.plugins.chats.api.dashboard_composition",
)


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
