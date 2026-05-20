"""Tests de `bootstrap_sales_session_activity` (F7 / PR-A workspace refactor).

La activity:
  * Saca del workflow Sales la I/O y la construccion del registry de tools.
  * Recibe un `SalesSessionInput` (PR-A: era `session_id: str`) y devuelve un
    `SessionInput` JSON-safe que cruza la frontera del @workflow.run.
  * PR-B: instancia `WorkspaceConfig(path=input.runtime_workspace_path)` y falla
    fast (`RuntimeError`) si el path no esta cableado por el composition root.

Cubierto:
  * Failfast cuando `runtime_workspace_path is None` (composition root miswired).
  * Happy path: retorna `SessionInput` con campos primitivos (R-JSON), el
    `workspace.path` apunta al workspace canonico del agente.
  * Idempotencia: dos ejecuciones consecutivas devuelven el mismo `workspace.path`.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from exoclaw_temporal.config import SessionInput

from src.plugins.chats.agent.sales.contracts import SalesSessionInput


def _build_workspace(path: Path) -> Path:
    """Crea un workspace canonico minimo (los 5 BOOTSTRAP_FILES del runtime)."""
    path.mkdir(parents=True, exist_ok=True)
    for fname in ("IDENTITY.md", "SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md"):
        (path / fname).write_text(f"# {fname} fixture\n", encoding="utf-8")
    return path


@pytest.fixture
def runtime_workspace(tmp_path: Path) -> Path:
    """Workspace canonico aislado a `tmp_path` para no tocar el del repo."""
    return _build_workspace(tmp_path / "workspace")


async def test_bootstrap_returns_json_safe_session_input(runtime_workspace: Path) -> None:
    from src.plugins.chats.agent.sales.activities import bootstrap_sales_session_activity

    env = ActivityEnvironment()
    result: SessionInput = await env.run(
        bootstrap_sales_session_activity,
        SalesSessionInput(
            session_id="wa_5491111111111",
            runtime_workspace_path=str(runtime_workspace),
        ),
    )

    assert isinstance(result, SessionInput)
    assert result.session_id == "wa_5491111111111"
    assert result.channel == "whatsapp"
    assert result.chat_id == "wa_5491111111111"
    # PR-B: el workspace que ve el runtime es el canonico (no el per-session vault).
    assert result.workspace.path == str(runtime_workspace)

    # R-JSON: el dataclass debe ser asdict-able (todos los campos JSON-friendly).
    payload = asdict(result)
    assert payload["session_id"] == "wa_5491111111111"
    assert payload["channel"] == "whatsapp"
    # `tool_definitions_json` es un string JSON valido (lista, posiblemente vacia
    # si el test no registra extensions — el registry base es vacio por default
    # post-Opcion-A: cada dominio registra sus tools via `register_tool_extension`
    # en su propio `worker.py`, no en el registry global).
    tool_defs = json.loads(payload["tool_definitions_json"])
    assert isinstance(tool_defs, list)


async def test_bootstrap_falls_back_to_local_workspace_when_path_missing() -> None:
    """ADR-2026-05-20: el dispatcher genérico no conoce el path del worker
    target (sería R-DIP #10). Cuando el caller cross-worker es el dispatcher
    declarativo, `runtime_workspace_path` viene en None y el bootstrap
    resuelve via `get_workspace_path()` (config del propio worker).

    Pre-ADR-2026-05-20 este caso lanzaba RuntimeError. Ahora hace fallback.
    El path resuelto es el default committed al repo (workspace/ del agente).
    """
    from src.plugins.chats.agent.sales.activities import bootstrap_sales_session_activity
    from src.plugins.chats.agent.sales.config.env import get_workspace_path

    env = ActivityEnvironment()
    result = await env.run(
        bootstrap_sales_session_activity,
        SalesSessionInput(
            session_id="wa_5499999999999",
            runtime_workspace_path=None,
        ),
    )
    # El bootstrap usó el path del config local — el resultado tiene un
    # workspace válido (no None ni vacío).
    assert result is not None
    # Sanity check: el path resuelto debe ser el del agente sales.
    expected_path = str(get_workspace_path())
    assert expected_path in str(result.workspace.path) or str(result.workspace.path) == expected_path


async def test_bootstrap_is_idempotent(runtime_workspace: Path) -> None:
    """Dos ejecuciones consecutivas con el mismo input devuelven el mismo
    `workspace.path` (no hay efectos colaterales que cambien entre calls)."""
    from src.plugins.chats.agent.sales.activities import bootstrap_sales_session_activity

    env = ActivityEnvironment()
    payload = SalesSessionInput(
        session_id="wa_5493333333333",
        runtime_workspace_path=str(runtime_workspace),
    )

    first: SessionInput = await env.run(bootstrap_sales_session_activity, payload)
    second: SessionInput = await env.run(bootstrap_sales_session_activity, payload)

    assert first.workspace.path == second.workspace.path
    assert first.workspace.path == str(runtime_workspace)
