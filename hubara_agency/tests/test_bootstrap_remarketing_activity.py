"""Tests de `bootstrap_remarketing_session_activity` (PR-A / PR-B).

La activity:
  * Saca del workflow Remarketing la I/O y la construccion del registry de tools.
  * Recibe un `RemarketingSessionInput` y devuelve un `SessionInput` JSON-safe
    que cruza la frontera del @workflow.run.
  * PR-B: instancia `WorkspaceConfig(path=input.runtime_workspace_path)` y falla
    fast (`RuntimeError`) si el path no esta cableado por el composition root.

Cubierto:
  * Failfast cuando `runtime_workspace_path is None` (composition root miswired).
  * Happy path: retorna `SessionInput` con campos primitivos (R-JSON), el
    `workspace.path` apunta al workspace canonico del agente de Remarketing.
  * Idempotencia: dos ejecuciones consecutivas devuelven el mismo `workspace.path`.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from exoclaw_temporal.config import SessionInput

from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput


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
    from src.plugins.chats.agent.remarketing.activities import (
        bootstrap_remarketing_session_activity,
    )

    env = ActivityEnvironment()
    result: SessionInput = await env.run(
        bootstrap_remarketing_session_activity,
        RemarketingSessionInput(
            session_id="wa_5491111111111",
            motivo="cliente dudó del precio",
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
    """
    from src.plugins.chats.agent.remarketing.activities import (
        bootstrap_remarketing_session_activity,
    )
    from src.plugins.chats.agent.remarketing.config.env import get_workspace_path

    env = ActivityEnvironment()
    result = await env.run(
        bootstrap_remarketing_session_activity,
        RemarketingSessionInput(
            session_id="wa_5499999999999",
            motivo="rebote",
            runtime_workspace_path=None,
        ),
    )
    assert result is not None
    expected_path = str(get_workspace_path())
    assert expected_path in str(result.workspace.path) or str(result.workspace.path) == expected_path


async def test_bootstrap_is_idempotent(runtime_workspace: Path) -> None:
    """Dos ejecuciones consecutivas con el mismo input devuelven el mismo
    `workspace.path` (no hay efectos colaterales que cambien entre calls)."""
    from src.plugins.chats.agent.remarketing.activities import (
        bootstrap_remarketing_session_activity,
    )

    env = ActivityEnvironment()
    payload = RemarketingSessionInput(
        session_id="wa_5493333333333",
        motivo="rebote",
        runtime_workspace_path=str(runtime_workspace),
    )

    first: SessionInput = await env.run(bootstrap_remarketing_session_activity, payload)
    second: SessionInput = await env.run(bootstrap_remarketing_session_activity, payload)

    assert first.workspace.path == second.workspace.path
    assert first.workspace.path == str(runtime_workspace)


async def test_bootstrap_uses_runtime_workspace_path_not_per_session_vault(
    runtime_workspace: Path,
) -> None:
    """PR-B regression: el `workspace.path` reportado por el SessionInput es el
    canonico del agente (passed as `runtime_workspace_path`), NO el per-session
    vault que devolvia `build_workspace_config(session_id)` pre-PR-B.
    """
    from src.plugins.chats.agent.remarketing.activities import (
        bootstrap_remarketing_session_activity,
    )

    env = ActivityEnvironment()
    result: SessionInput = await env.run(
        bootstrap_remarketing_session_activity,
        RemarketingSessionInput(
            session_id="wa_5494444444444",
            motivo="silencio",
            runtime_workspace_path=str(runtime_workspace),
        ),
    )

    # El path no debe contener el session_id (hubiera sido el del per-session vault).
    assert "wa_5494444444444" not in result.workspace.path
    assert result.workspace.path == str(runtime_workspace)
