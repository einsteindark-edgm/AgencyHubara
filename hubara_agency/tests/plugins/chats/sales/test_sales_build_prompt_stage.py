"""Override de `build_prompt` del worker Sales: inyección de guion POR ETAPA.

Dieta de prompt (runs eda8d460/019f24bf): el guion completo viajaba entero en
cada llamada. El override (`sales_build_prompt`, registrado con el MISMO
nombre de activity "build_prompt" — cero cambio de shape del workflow, cero
implicación de replay) resuelve la etapa del funnel de forma determinista
desde `metadata.json` (order_draft del episodio activo) y pasa
`skills=[<etapa>]` al ContextBuilder → el system prompt lleva SOLO el guion
de la etapa actual como Active Skill.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from exoclaw_temporal.config import BuildPromptInput, LLMConfig, WorkspaceConfig

from src.plugins.chats.agent.sales.activities.build_prompt_stage import (
    sales_build_prompt,
)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for stage, marker in (
        ("etapa_descubrimiento", "MARKER_DESCUBRIMIENTO"),
        ("etapa_variantes", "MARKER_VARIANTES"),
    ):
        d = ws / "skills" / stage
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\ndescription: guion {stage}\n---\n\n{marker}\n",
            encoding="utf-8",
        )
    core = ws / "skills" / "sales_script"
    core.mkdir(parents=True)
    (core / "SKILL.md").write_text(
        '---\ndescription: core\nmetadata: {"exoclaw": {"always": true}}\n---\n\n'
        "MARKER_CORE\n",
        encoding="utf-8",
    )
    return ws


def _seed_metadata(vault: Path, session_id: str, slots: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "episodes": [
            {"id": "ep_001", "opened_at_ms": 1, "order_draft": {"slots": slots}}
        ]
    }
    (d / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _input(ws: Path, session_id: str) -> BuildPromptInput:
    return BuildPromptInput(
        session_id=session_id,
        message="quiero el difusor",
        channel="whatsapp",
        chat_id=session_id,
        llm=LLMConfig(model="fake"),
        workspace=WorkspaceConfig(path=str(ws)),
        media=None,
        plugin_context=None,
    )


@pytest.mark.asyncio
async def test_injects_only_active_stage_skill(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    """Etapa variantes (producto sin aroma/color/cantidad) → el system prompt
    lleva el guion de variantes + el core, y NO el de descubrimiento."""
    ws = _make_workspace(tmp_path)
    _seed_metadata(
        _isolate_vault_dir, "wa_stage1", {"producto": "Plegaria de Luz"}
    )
    messages = await sales_build_prompt(_input(ws, "wa_stage1"))
    system = messages[0]["content"]
    assert "MARKER_CORE" in system  # el core always:true sigue presente
    assert "MARKER_VARIANTES" in system
    assert "MARKER_DESCUBRIMIENTO" not in system


@pytest.mark.asyncio
async def test_no_metadata_defaults_to_descubrimiento(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    """Sesión sin metadata (primer contacto) → guion de descubrimiento."""
    ws = _make_workspace(tmp_path)
    messages = await sales_build_prompt(_input(ws, "wa_stage_new"))
    system = messages[0]["content"]
    assert "MARKER_DESCUBRIMIENTO" in system
    assert "MARKER_VARIANTES" not in system
