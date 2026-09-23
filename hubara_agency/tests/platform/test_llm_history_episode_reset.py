"""Corte del historial del LLM al empezar un episodio que lo pide.

Caso 2026-09-22 (runs edbb0d8b / 8e73b7dc): el cliente respondió a una
campaña (episodio nuevo ep_005), pero el historial del LLM de exoclaw es por
SESIÓN, no por episodio: ventas y remarketing siguieron viendo decenas de
turnos sobre la Trilogía del episodio anterior. Ventas le ofreció la
Trilogía, el cierre por ghosting la anotó en el motivo y el remarketing le
escribió "Te quedó sonando la Trilogía del Terror… ¿La retomamos?".

Contrato: si el episodio activo trae `llm_history_reset`, la PRIMERA vez que
cada agente arma un prompt en ese episodio su historial se corta en ese punto
(el puntero `last_consolidated` de exoclaw: lo anterior queda en disco pero no
viaja al LLM). Idempotente por agente: los turnos siguientes del episodio no se
vuelven a cortar.

Sin "Previous Session Summary" (run 28a8e407, 2026-09-23): exoclaw pega ese
`summary` delante de CADA mensaje del cliente y lo graba con él — el motivo
viejo de la Trilogía quedó repetido en todo el episodio. Lo anterior viaja una
sola vez, en el primer mensaje del episodio (lo arma el ingest).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from exoclaw_conversation.session.manager import SessionManager
from exoclaw_temporal.activities.conversation import (
    _build_conversation,
    _state_workspace_for,
)
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig
from src.platform.llm_history_reset import (
    ResetLLMHistoryInput,
    reset_llm_history_for_episode_activity,
)

_SESSION = "wa_573001234567"
_OLD_TURNS = [
    {"role": "user", "content": "Quiero la Trilogía del Terror"},
    {"role": "assistant", "content": "¡Claro! ¿Me pasas tus datos de envío?"},
]
_SUMMARY = "Conversación anterior (cerrada): carrito web con la Trilogía del Terror."


def _llm() -> LLMConfig:
    return LLMConfig(model="m", api_key="k", api_base="http://litellm.invalid:4000")


def _workspace(tmp_path: Path, name: str) -> WorkspaceConfig:
    ws = tmp_path / name / "workspace"
    ws.mkdir(parents=True)
    return WorkspaceConfig(path=str(ws))


def _write_metadata(vault: Path, data: dict) -> None:
    (vault / _SESSION).mkdir(parents=True, exist_ok=True)
    (vault / _SESSION / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read_metadata(vault: Path) -> dict:
    return json.loads((vault / _SESSION / "metadata.json").read_text(encoding="utf-8"))


def _episodes(reset: dict | None) -> dict:
    new_ep = {"episode_id": "ep_005", "closed_at_ms": None}
    if reset is not None:
        new_ep["llm_history_reset"] = reset
    return {
        "episodes": [
            {"episode_id": "ep_004", "closed_at_ms": 10, "closing_tag": "CAMPAIGN_REPLY"},
            new_ep,
        ]
    }


async def _prompt_texts(ws: WorkspaceConfig) -> str:
    conv = _build_conversation(_llm(), ws)
    messages = await conv.build_prompt(_SESSION, "Me gusta")
    return "\n".join(str(m.get("content")) for m in messages)


@pytest.fixture()
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_state"
    root.mkdir()
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(root))
    return root


def _set_exoclaw_summary(ws: WorkspaceConfig, summary: str) -> None:
    """Lo que dejaba el corte del #330 en la sesión de exoclaw."""
    manager = SessionManager(_state_workspace_for(Path(ws.path)) or Path(ws.path))
    session = manager.get_or_create(_SESSION)
    session.metadata["summary"] = summary
    manager.save_metadata(session)


@pytest.mark.asyncio
async def test_new_episode_cuts_the_llm_history_without_pasting_a_summary(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    ws = _workspace(tmp_path, "sales")
    await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    # Pedido con la forma del #330 (traía `summary`): se corta igual, pero el
    # resumen ya no se inyecta.
    _write_metadata(_isolate_vault_dir, _episodes({"summary": _SUMMARY, "applied": []}))

    cut = await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)
    )

    assert cut is True
    prompt = await _prompt_texts(ws)
    assert "Quiero la Trilogía del Terror" not in prompt
    assert "¿Me pasas tus datos de envío?" not in prompt
    assert _SUMMARY not in prompt
    assert "Previous Session Summary" not in prompt
    # Queda anotado para este agente: no se vuelve a cortar.
    applied = _read_metadata(_isolate_vault_dir)["episodes"][-1]["llm_history_reset"][
        "applied"
    ]
    assert applied == [ws.path]


@pytest.mark.asyncio
async def test_turns_of_the_new_episode_survive_the_next_call(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    ws = _workspace(tmp_path, "sales")
    conv = _build_conversation(_llm(), ws)
    await conv.record(_SESSION, _OLD_TURNS)
    _write_metadata(_isolate_vault_dir, _episodes({"summary": _SUMMARY, "applied": []}))
    inp = ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)

    await reset_llm_history_for_episode_activity(inp)
    await _build_conversation(_llm(), ws).record(
        _SESSION,
        [
            {"role": "user", "content": "Me gusta el cubo love"},
            {"role": "assistant", "content": "¡Es precioso! ¿En qué aroma?"},
        ],
    )
    second = await reset_llm_history_for_episode_activity(inp)

    assert second is False
    prompt = await _prompt_texts(ws)
    assert "Me gusta el cubo love" in prompt
    assert "Quiero la Trilogía del Terror" not in prompt


@pytest.mark.asyncio
async def test_each_agent_cuts_its_own_history_once(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    sales = _workspace(tmp_path, "sales")
    remarketing = _workspace(tmp_path, "remarketing")
    for ws in (sales, remarketing):
        await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    _write_metadata(_isolate_vault_dir, _episodes({"summary": _SUMMARY, "applied": []}))

    await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=sales.path)
    )
    assert "Quiero la Trilogía" in await _prompt_texts(remarketing)

    cut = await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=remarketing.path)
    )
    assert cut is True
    assert "Quiero la Trilogía" not in await _prompt_texts(remarketing)


@pytest.mark.asyncio
async def test_agent_without_history_yet_is_marked_and_later_turns_are_kept(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    ws = _workspace(tmp_path, "remarketing")
    _write_metadata(_isolate_vault_dir, _episodes({"summary": _SUMMARY, "applied": []}))
    inp = ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)

    assert await reset_llm_history_for_episode_activity(inp) is False
    await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    assert await reset_llm_history_for_episode_activity(inp) is False
    assert "Quiero la Trilogía" in await _prompt_texts(ws)


@pytest.mark.asyncio
async def test_episode_without_reset_request_leaves_history_alone(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    ws = _workspace(tmp_path, "sales")
    await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    _write_metadata(_isolate_vault_dir, _episodes(None))

    cut = await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)
    )

    assert cut is False
    assert "Quiero la Trilogía del Terror" in await _prompt_texts(ws)


@pytest.mark.asyncio
async def test_without_state_dir_cuts_the_workspace_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path
) -> None:
    """Dev/tests sin EXOCLAW_STATE_DIR: el historial vive en el workspace."""
    monkeypatch.delenv("EXOCLAW_STATE_DIR", raising=False)
    ws = _workspace(tmp_path, "sales")
    await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    _write_metadata(_isolate_vault_dir, _episodes({"summary": _SUMMARY, "applied": []}))

    await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)
    )

    assert "Quiero la Trilogía del Terror" not in await _prompt_texts(ws)


@pytest.mark.asyncio
async def test_summary_left_by_the_previous_cut_is_removed_on_the_next_turn(
    tmp_path: Path, state_dir: Path, _isolate_vault_dir: Path
) -> None:
    """Sesiones cortadas con el #330 quedaron con `summary` en exoclaw: se
    pegaba a cada mensaje nuevo. El turno siguiente lo borra aunque el
    episodio ya no pida corte (sin tocar el historial)."""
    ws = _workspace(tmp_path, "sales")
    await _build_conversation(_llm(), ws).record(_SESSION, _OLD_TURNS)
    _set_exoclaw_summary(ws, _SUMMARY)
    _write_metadata(_isolate_vault_dir, _episodes(None))
    assert _SUMMARY in await _prompt_texts(ws)

    cut = await reset_llm_history_for_episode_activity(
        ResetLLMHistoryInput(session_id=_SESSION, workspace_path=ws.path)
    )

    assert cut is False
    prompt = await _prompt_texts(ws)
    assert _SUMMARY not in prompt
    assert "Quiero la Trilogía del Terror" in prompt
