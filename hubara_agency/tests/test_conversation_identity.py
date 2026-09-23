"""El system prompt no abre con la identidad genérica de exoclaw (run 28a8e407).

`exoclaw_conversation.ContextBuilder._get_identity` encabeza TODO system prompt
con "You are exoclaw, a helpful AI assistant", rutas de MEMORY.md/HISTORY.md,
guías de edición de archivos y — la que dolía — "State intent before tool
calls": le pedía al modelo narrar antes de cada tool. Con thinking apagado esa
narración vive en su texto, queda en su historial y termina colándose en el
mensaje al cliente ("El cliente pregunta… Le aclaro…").

Contrato (choke point `_build_conversation`: build_prompt genérico, el
override de ventas y record_turn — ventas, remarketing, eta): el prompt NO
lleva nada de ese encabezado y SÍ sigue llevando los archivos del agente.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig

_SALES_WORKSPACE = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/chats/agent/sales/workspace"
)

_GENERIC = (
    "You are exoclaw",
    "State intent before tool calls",
    "Reply directly with text",
    "Only use the 'message' tool",
    "Before modifying a file",
    "memory/MEMORY.md",
)


async def _system_prompt(workspace: Path) -> str:
    llm = LLMConfig(model="m", api_key="k", api_base="http://litellm.invalid:4000")
    conv = _build_conversation(llm, WorkspaceConfig(path=str(workspace)))
    messages = await conv.build_prompt("wa_573001234567", "Hola")
    return str(messages[0]["content"])


@pytest.mark.asyncio
async def test_prompt_has_no_generic_exoclaw_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(tmp_path / "state"))
    system = await _system_prompt(_SALES_WORKSPACE)

    leftovers = [line for line in _GENERIC if line in system]
    assert leftovers == [], f"el encabezado genérico sigue en el prompt: {leftovers}"


@pytest.mark.asyncio
async def test_prompt_keeps_the_agent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(tmp_path / "state"))
    system = await _system_prompt(_SALES_WORKSPACE)

    assert "## SOUL.md" in system
    assert "Asesor de Ventas Hubara" in system
