"""Las notas del turno viajan con el mensaje del turno, no en las instrucciones.

Medido contra la API de DeepSeek (2026-10-06): su caché es de prefijo estricto
y las tools van DESPUÉS del system. La hora de Bogotá (al minuto) y los DATOS
DEL PEDIDO iban en `# Retrieved Context`, a mitad de las instrucciones: en la
primera llamada de cada turno solo 45 % del prompt salía del caché (el resto
del system + las 24 tools, ~15k tokens, a precio completo). Con las
instrucciones idénticas turno a turno y las notas en el mensaje del turno, 99 %.

El interruptor `SALES_PROMPT_TURN_CONTEXT` (Terraform, lab-config) enciende por
etapas: `off` (como antes) → `team` (solo `LAB_INTERNAL_NUMBERS`) → `on`.

El historial durable guarda SOLO lo que escribió el cliente: las notas van
dentro del bloque `[Runtime Context]` que exoclaw ya recorta al grabar, así la
hora vieja y el pedido viejo no se acumulan turno a turno.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import pytest

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import BuildPromptInput, LLMConfig, WorkspaceConfig

from src.plugins.chats.agent.sales.activities.build_prompt_stage import (
    sales_build_prompt,
)
from src.plugins.chats.agent.sales.context import (
    _resolve_bogota_tz,
    build_bogota_context_string,
)

TEAM = "wa_10000000001"
CUSTOMER = "wa_10000000002"
TEXT = "quiero el difusor"
# PNG 1x1 válido: exoclaw solo adjunta la imagen si reconoce el mime.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    stage = ws / "skills" / "etapa_variantes"
    stage.mkdir(parents=True)
    (stage / "SKILL.md").write_text(
        "---\ndescription: guion variantes\n---\n\nMARKER_VARIANTES\n", encoding="utf-8"
    )
    core = ws / "skills" / "sales_script"
    core.mkdir(parents=True)
    (core / "SKILL.md").write_text(
        '---\ndescription: core\nmetadata: {"exoclaw": {"always": true}}\n---\n\nMARKER_CORE\n',
        encoding="utf-8",
    )
    return ws


def _seed(vault: Path, session_id: str, slots: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"episodes": [{"id": "ep_001", "opened_at_ms": 1, "order_draft": {"slots": slots}}]}
    (d / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _clock(hour: int, minute: int) -> str:
    return build_bogota_context_string(
        datetime(2026, 10, 6, hour, minute, tzinfo=_resolve_bogota_tz())
    )


def _input(ws: Path, session_id: str, clock: str, media: list[str] | None = None) -> BuildPromptInput:
    return BuildPromptInput(
        session_id=session_id,
        message=TEXT,
        channel="whatsapp",
        chat_id=session_id,
        llm=LLMConfig(model="fake"),
        workspace=WorkspaceConfig(path=str(ws)),
        media=media,
        plugin_context=[clock],
    )


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))  # type: ignore[union-attr]


@pytest.fixture
def notes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALES_PROMPT_TURN_CONTEXT", "on")


@pytest.mark.asyncio
async def test_instructions_are_identical_from_one_turn_to_the_next(
    tmp_path: Path, _isolate_vault_dir: Path, notes_on: None
) -> None:
    """Otro minuto y otro dato del pedido: las instrucciones no cambian (el
    caché de DeepSeek cubre el system completo y las tools)."""
    ws = _workspace(tmp_path)
    _seed(_isolate_vault_dir, CUSTOMER, {"producto": "Plegaria de Luz"})
    first = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))
    _seed(_isolate_vault_dir, CUSTOMER, {"producto": "Plegaria de Luz", "aroma": "Sándalo"})
    second = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 23)))

    assert first[0]["role"] == "system"
    assert first[0]["content"] == second[0]["content"]
    assert "Hora actual en Colombia" not in first[0]["content"]
    assert "DATOS DEL PEDIDO" not in first[0]["content"]
    assert "MARKER_VARIANTES" in first[0]["content"]  # el guion de la etapa sigue ahí


@pytest.mark.asyncio
async def test_the_turn_notes_reach_the_model_with_the_turn_message(
    tmp_path: Path, _isolate_vault_dir: Path, notes_on: None
) -> None:
    ws = _workspace(tmp_path)
    _seed(_isolate_vault_dir, CUSTOMER, {"producto": "Plegaria de Luz", "aroma": "Sándalo"})
    messages = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))

    turn = messages[-1]
    assert turn["role"] == "user"
    content = _text_of(turn["content"])
    assert "Hora actual en Colombia (America/Bogota): 10:22" in content
    assert "DATOS DEL PEDIDO" in content and "Sándalo" in content
    assert content.endswith(TEXT)  # lo que escribió el cliente va al final


@pytest.mark.asyncio
async def test_the_history_keeps_only_what_the_customer_wrote(
    tmp_path: Path, _isolate_vault_dir: Path, notes_on: None
) -> None:
    """exoclaw graba el mensaje del turno: la hora y el pedido de ESE turno no
    pueden quedar en el historial (se acumularían, viejos, turno a turno)."""
    ws = _workspace(tmp_path)
    _seed(_isolate_vault_dir, CUSTOMER, {"producto": "Plegaria de Luz"})
    llm, workspace = LLMConfig(model="fake"), WorkspaceConfig(path=str(ws))
    messages = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))

    conv = _build_conversation(llm, workspace)
    await conv.record(CUSTOMER, [messages[-1]])
    [saved] = conv.history.get_or_create(CUSTOMER).get_history(max_messages=10)
    assert saved["role"] == "user"
    assert saved["content"] == TEXT


@pytest.mark.asyncio
async def test_a_photo_turn_also_keeps_the_notes_out_of_the_history(
    tmp_path: Path, _isolate_vault_dir: Path, notes_on: None
) -> None:
    ws = _workspace(tmp_path)
    photo = tmp_path / "foto.png"
    photo.write_bytes(_PNG)
    _seed(_isolate_vault_dir, CUSTOMER, {"producto": "Plegaria de Luz"})
    messages = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22), media=[str(photo)]))

    assert "Hora actual en Colombia" in _text_of(messages[-1]["content"])
    conv = _build_conversation(LLMConfig(model="fake"), WorkspaceConfig(path=str(ws)))
    await conv.record(CUSTOMER, [messages[-1]])
    [saved] = conv.history.get_or_create(CUSTOMER).get_history(max_messages=10)
    saved_text = _text_of(saved["content"])
    assert "Hora actual" not in saved_text and "DATOS DEL PEDIDO" not in saved_text
    assert TEXT in saved_text


@pytest.mark.asyncio
async def test_off_by_default_the_notes_stay_in_the_instructions(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SALES_PROMPT_TURN_CONTEXT", raising=False)
    ws = _workspace(tmp_path)
    messages = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))
    assert "# Retrieved Context" in messages[0]["content"]
    assert "Hora actual en Colombia" in messages[0]["content"]


@pytest.mark.asyncio
async def test_team_mode_moves_the_notes_only_for_team_numbers(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`team`: el piloto con los teléfonos del equipo (E.164 de Terraform,
    comparados por dígitos); los clientes siguen como antes."""
    monkeypatch.setenv("SALES_PROMPT_TURN_CONTEXT", "team")
    monkeypatch.setenv("LAB_INTERNAL_NUMBERS", "+10000000001, +19999999999")
    ws = _workspace(tmp_path)
    team = await sales_build_prompt(_input(ws, TEAM, _clock(10, 22)))
    customer = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))

    assert "Hora actual en Colombia" not in team[0]["content"]
    assert "Hora actual en Colombia" in _text_of(team[-1]["content"])
    assert "Hora actual en Colombia" in customer[0]["content"]


@pytest.mark.asyncio
async def test_an_unknown_value_keeps_the_notes_in_the_instructions(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SALES_PROMPT_TURN_CONTEXT", "si")
    ws = _workspace(tmp_path)
    messages = await sales_build_prompt(_input(ws, CUSTOMER, _clock(10, 22)))
    assert "Hora actual en Colombia" in messages[0]["content"]
