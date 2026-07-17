"""El historial LLM sobrevive a los deploys — `EXOCLAW_STATE_DIR`.

Incidente 2026-07-17 (run 019f6db3, sesión wa_573125671604): el historial de
conversación de exoclaw (`<workspace>/sessions/*.jsonl`) vive en el
filesystem del CONTAINER (el workspace viaja en la imagen). El deploy de las
00:55Z recreó los workers y borró la memoria conversacional de TODOS los
clientes activos; 36 min después el cliente respondió al remarketing con
"Vamos con esas dos" y el bot no supo cuáles eran (2ª vez que un deploy
destruye historial en prod).

Contrato nuevo (choke point único `_build_conversation`, usado por TODOS los
workers exoclaw — sales, remarketing, eta, sentinel, reengagement, eval):

  * `EXOCLAW_STATE_DIR` seteado → las sessions se leen/escriben bajo
    `$EXOCLAW_STATE_DIR/<slug(workspace)>/sessions/` — en prod ese root vive
    en el volumen hubara-vault → inmune a deploys.
  * Slug POR workspace: sales y remarketing comparten session_ids (wa_<phone>)
    → sin aislamiento por agente se mezclarían historiales.
  * Env var ausente → comportamiento legacy intacto (dev/tests sin cambios).
  * Los prompts/skills siguen leyéndose del workspace de CÓDIGO (imagen):
    solo el ESTADO se muda.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig


def _llm() -> LLMConfig:
    return LLMConfig(
        model="test-model",
        api_key="test-key",
        api_base="http://litellm.invalid:4000",
    )


def _workspace(tmp_path: Path, name: str) -> WorkspaceConfig:
    ws = tmp_path / name / "workspace"
    ws.mkdir(parents=True)
    return WorkspaceConfig(path=str(ws))


_TURN = [
    {"role": "user", "content": "Vamos con esas dos"},
    {"role": "assistant", "content": "Leo café y Libra sándalo, ¿confirmamos?"},
]


@pytest.mark.asyncio
async def test_env_unset_keeps_sessions_in_workspace(tmp_path, monkeypatch):
    """Legacy intacto: sin la env var, sessions bajo <workspace>/sessions."""
    monkeypatch.delenv("EXOCLAW_STATE_DIR", raising=False)
    ws = _workspace(tmp_path, "sales")
    conv = _build_conversation(_llm(), ws)
    await conv.record("wa_57310000000", _TURN)
    assert list((Path(ws.path) / "sessions").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_env_set_moves_sessions_to_state_dir(tmp_path, monkeypatch):
    state = tmp_path / "vault" / "agent_state"
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(state))
    ws = _workspace(tmp_path, "sales")

    conv = _build_conversation(_llm(), ws)
    await conv.record("wa_57310000000", _TURN)

    jsonls = list(state.rglob("*.jsonl"))
    assert jsonls, f"no session file under {state}"
    # El workspace de código queda LIMPIO — el estado ya no vive ahí.
    assert not list((Path(ws.path) / "sessions").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_history_survives_new_conversation_instance(tmp_path, monkeypatch):
    """Roundtrip: una instancia NUEVA (— un worker recién deployado —) lee el
    historial persistido en el state dir."""
    state = tmp_path / "vault" / "agent_state"
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(state))
    ws = _workspace(tmp_path, "sales")

    await _build_conversation(_llm(), ws).record("wa_57310000000", _TURN)
    fresh = _build_conversation(_llm(), ws)
    session = fresh.history.get_or_create("wa_57310000000")
    assert session.total_messages == len(_TURN)


@pytest.mark.asyncio
async def test_state_isolated_per_workspace(tmp_path, monkeypatch):
    """Sales y remarketing usan el MISMO session_id (wa_<phone>) — sus
    historiales NO deben mezclarse en el state dir."""
    state = tmp_path / "vault" / "agent_state"
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(state))
    ws_sales = _workspace(tmp_path, "sales")
    ws_remk = _workspace(tmp_path, "remarketing")

    await _build_conversation(_llm(), ws_sales).record("wa_1", _TURN)
    remk = _build_conversation(_llm(), ws_remk)
    assert remk.history.get_or_create("wa_1").total_messages == 0
