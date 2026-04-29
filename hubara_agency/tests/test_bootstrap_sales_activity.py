"""Tests de `bootstrap_sales_session_activity` (F7).

Saca del workflow Sales la I/O (`Path.mkdir`) y la construccion del registry
de tools, devolviendo un `SessionInput` JSON-safe que cruza la frontera del
@workflow.run. Simetrico al test de Remarketing (PR F6.1).

Cubierto:
  * La activity se ejecuta dentro de `ActivityEnvironment` y retorna un
    `SessionInput` con campos primitivos (R-JSON).
  * El filesystem se setupea: el `workspace.path` apunta a una dir que existe.
  * Es idempotente (mkdir(exist_ok=True)): dos ejecuciones consecutivas no
    fallan ni cambian el path.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from exoclaw_temporal.config import SessionInput


@pytest.fixture
def isolated_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Aisla el WORKSPACE_VAULT_DIR a `tmp_path` para no tocar el vault real."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # `build_workspace_config` lee `WORKSPACE_VAULT_DIR` del modulo
    # `src.core.config` via `from src.core.config import ... WORKSPACE_VAULT_DIR`;
    # ya esta importado en `src.core.registries`. Reemplazamos la referencia
    # ya importada alli.
    monkeypatch.setattr("src.core.registries.WORKSPACE_VAULT_DIR", vault)
    return vault


async def test_bootstrap_returns_json_safe_session_input(isolated_workspace: Path) -> None:
    from src.domains.sales_whatsapp.activities import bootstrap_sales_session_activity

    env = ActivityEnvironment()
    result: SessionInput = await env.run(
        bootstrap_sales_session_activity,
        "wa_5491111111111",
    )

    assert isinstance(result, SessionInput)
    assert result.session_id == "wa_5491111111111"
    assert result.channel == "whatsapp"
    assert result.chat_id == "wa_5491111111111"

    # R-JSON: el dataclass debe ser asdict-able (todos los campos JSON-friendly).
    payload = asdict(result)
    assert payload["session_id"] == "wa_5491111111111"
    assert payload["channel"] == "whatsapp"
    # `tool_definitions_json` es un string JSON valido con al menos una tool registrada.
    tool_defs = json.loads(payload["tool_definitions_json"])
    assert isinstance(tool_defs, list)
    assert len(tool_defs) >= 1


async def test_bootstrap_creates_workspace_dir(isolated_workspace: Path) -> None:
    from src.domains.sales_whatsapp.activities import bootstrap_sales_session_activity

    env = ActivityEnvironment()
    result: SessionInput = await env.run(
        bootstrap_sales_session_activity,
        "wa_5492222222222",
    )

    # `build_workspace_config` debe haber hecho `mkdir(parents=True, exist_ok=True)`.
    workspace_path = Path(result.workspace.path)
    assert workspace_path.exists()
    assert workspace_path.is_dir()
    # La dir debe estar bajo el vault aislado.
    assert (
        isolated_workspace in workspace_path.parents
        or workspace_path == isolated_workspace / "wa_5492222222222"
    )


async def test_bootstrap_is_idempotent(isolated_workspace: Path) -> None:
    """mkdir(exist_ok=True) implica que dos ejecuciones consecutivas no fallan."""
    from src.domains.sales_whatsapp.activities import bootstrap_sales_session_activity

    env = ActivityEnvironment()
    first: SessionInput = await env.run(
        bootstrap_sales_session_activity,
        "wa_5493333333333",
    )
    second: SessionInput = await env.run(
        bootstrap_sales_session_activity,
        "wa_5493333333333",
    )

    assert first.workspace.path == second.workspace.path
