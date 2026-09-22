"""Ninguna conversación ve datos de otra — sin `MEMORY.md` compartido.

Incidente 2026-09-17 (run 7889b9f6): exoclaw_conversation consolida en
background cualquier sesión con ≥ `memory_window` mensajes: un LLM resume la
conversación y la escribe en `<workspace>/memory/MEMORY.md` — un archivo POR
AGENTE, compartido por TODOS los clientes — y ese archivo se inyecta en la
sección `# Memory` del system prompt de TODAS las conversaciones. En prod el
prompt de un cliente llevó el pedido pendiente de otra clienta ("Duo Zodiacal
Cáncer morado + Velón Amor Eterno") y "reglas de negocio" redactadas por el LLM.

Contrato (choke point `_build_conversation`, usado por build_prompt genérico,
el override de ventas y record_turn — sales, remarketing, eta):

  * La consolidación NUNCA se dispara: no hay llamada al LLM de resumen ni
    escritura en `MEMORY.md`/`HISTORY.md`, por larga que sea la sesión.
  * El system prompt NUNCA lleva `MEMORY.md`, aunque el archivo tenga
    contenido (quedó de antes, alguien lo editó, etc.).

Determinista: no depende de que el LLM "no escriba" nada — el LLM de resumen
ni siquiera se invoca (se verifica con un provider falso que SÍ escribiría).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from exoclaw_provider_litellm.provider import LiteLLMProvider

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig

_CANARY = "CANARY-PEDIDO-DE-OTRA-CLIENTA"


class _LeakyLLM:
    """Registro de llamadas a un LLM falso que, si lo invocan para consolidar,
    escribe el canary en la memoria compartida — exactamente lo que hizo el
    LLM real en prod. Se parchea `chat` en la CLASE del provider real, así
    cualquier instancia que se construya (hoy o mañana) queda cubierta."""

    calls: list[dict[str, Any]] = []

    @staticmethod
    async def chat(self: Any, **kwargs: Any) -> Any:
        _LeakyLLM.calls.append(kwargs)
        return SimpleNamespace(
            has_tool_calls=True,
            tool_calls=[
                SimpleNamespace(
                    arguments={
                        "history_entry": f"[2026-09-17 16:22] {_CANARY}",
                        "memory_update": f"## Clientes recurrentes\n- {_CANARY}",
                    }
                )
            ],
        )


@pytest.fixture(params=["legacy", "state_dir"])
def env_mode(request, tmp_path, monkeypatch) -> str:
    """Las dos ramas de `_build_conversation`: sin y con EXOCLAW_STATE_DIR."""
    if request.param == "state_dir":
        monkeypatch.setenv("EXOCLAW_STATE_DIR", str(tmp_path / "vault" / "agent_state"))
    else:
        monkeypatch.delenv("EXOCLAW_STATE_DIR", raising=False)
    return request.param


@pytest.fixture
def leaky_provider(monkeypatch) -> type[_LeakyLLM]:
    _LeakyLLM.calls = []
    monkeypatch.setattr(LiteLLMProvider, "chat", _LeakyLLM.chat)
    return _LeakyLLM


def _llm() -> LLMConfig:
    return LLMConfig(model="test-model", api_key="k", api_base="http://litellm.invalid")


def _workspace(tmp_path: Path) -> WorkspaceConfig:
    ws = tmp_path / "sales" / "workspace"
    (ws / "memory").mkdir(parents=True)
    return WorkspaceConfig(path=str(ws))


def _long_conversation(n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n // 2):
        out.append({"role": "user", "content": f"Quiero el Duo Zodiacal Cáncer ({i})"})
        out.append({"role": "assistant", "content": f"¡Claro! ¿Qué color? ({i})"})
    return out


async def _drain_background_tasks() -> None:
    """Deja correr cualquier tarea de fondo que `build_prompt` haya lanzado."""
    for _ in range(20):
        await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=2)


def _system_prompt(messages: list[dict[str, Any]]) -> str:
    assert messages[0]["role"] == "system"
    content = messages[0]["content"]
    return content if isinstance(content, str) else str(content)


@pytest.mark.asyncio
async def test_long_session_does_not_leak_into_another_customer(
    tmp_path, env_mode, leaky_provider
):
    """El escenario de prod de punta a punta: la clienta A pasa de 100
    mensajes, y el prompt del cliente B NO lleva nada de A."""
    ws = _workspace(tmp_path)
    await _build_conversation(_llm(), ws).record("wa_A", _long_conversation(150))

    await _build_conversation(_llm(), ws).build_prompt("wa_A", "¿y en morado?")
    await _drain_background_tasks()

    prompt_b = await _build_conversation(_llm(), ws).build_prompt("wa_B", "Hola")

    assert _CANARY not in _system_prompt(prompt_b)
    assert leaky_provider.calls == [], "se invocó al LLM de consolidación"
    memory_dir = Path(ws.path) / "memory"
    for name in ("MEMORY.md", "HISTORY.md"):
        path = memory_dir / name
        assert not path.exists() or _CANARY not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_long_session_keeps_full_window_visible(tmp_path, env_mode, leaky_provider):
    """Sin consolidación la ventana no se recorta por `last_consolidated`: la
    sesión larga sigue viendo sus últimos `memory_window` mensajes, turno a
    turno (en prod una sesión de 702 mensajes veía solo 79)."""
    ws = _workspace(tmp_path)
    await _build_conversation(_llm(), ws).record("wa_A", _long_conversation(150))

    for _ in range(2):
        await _build_conversation(_llm(), ws).build_prompt("wa_A", "¿y en morado?")
        await _drain_background_tasks()

    session = _build_conversation(_llm(), ws).history.get_or_create("wa_A")
    assert session.last_consolidated == 0
    assert len(session.get_history(max_messages=_llm().memory_window)) == 100


@pytest.mark.asyncio
async def test_existing_memory_file_never_reaches_the_prompt(tmp_path, env_mode):
    """Aunque `MEMORY.md` tenga contenido (quedó de antes o alguien lo editó),
    el system prompt no lo inyecta."""
    ws = _workspace(tmp_path)
    (Path(ws.path) / "memory" / "MEMORY.md").write_text(
        f"# Long-term Memory\n\n- {_CANARY}\n", encoding="utf-8"
    )

    messages = await _build_conversation(_llm(), ws).build_prompt("wa_B", "Hola")

    system = _system_prompt(messages)
    assert _CANARY not in system
    assert "# Memory" not in system


_AGENT_WORKSPACES = sorted(
    (Path(__file__).resolve().parents[1] / "src" / "plugins").glob("*/agent/*/workspace")
)


@pytest.mark.parametrize("workspace", _AGENT_WORKSPACES, ids=lambda p: p.parent.name)
def test_agent_workspace_has_no_shared_memory(workspace: Path):
    """Ningún workspace de agente trae `memory/` ni le pide al LLM anotar
    datos en MEMORY.md/HISTORY.md: sería memoria compartida entre clientes
    (y ya ni se lee — ver `_NoSharedMemory`)."""
    assert not (workspace / "memory").exists()
    for md in workspace.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        assert "MEMORY.md" not in text and "HISTORY.md" not in text, md.name
