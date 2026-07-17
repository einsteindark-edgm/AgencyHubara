"""Conversation activities — build prompts and record turn history.

Uses DefaultConversation from exoclaw-conversation, same as nanobot.

DÓNDE VIVE EL HISTORIAL (incidente 2026-07-17, run 019f6db3):
por default las sessions JSONL van a `<workspace>/sessions/` — pero el
workspace viaja DENTRO de la imagen del container, así que cada deploy
borraba la memoria conversacional de todos los clientes activos (2 veces
en prod). Con `EXOCLAW_STATE_DIR` seteado (prod: un path dentro del volumen
persistente), el ESTADO (sessions) se muda a
`$EXOCLAW_STATE_DIR/<slug-del-workspace>/sessions/` — con un subdir por
workspace porque agentes distintos (sales/remarketing) comparten
session_ids (`wa_<phone>`) y sin aislamiento se mezclarían.

Los prompts/skills (ContextBuilder) y la memoria consolidada (MemoryStore)
siguen leyéndose del workspace de CÓDIGO: solo el historial se muda.
Env var ausente → comportamiento legacy intacto (dev/tests).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from exoclaw_conversation.conversation import DefaultConversation
from exoclaw_provider_litellm.provider import LiteLLMProvider
from temporalio import activity

from exoclaw_temporal.config import BuildPromptInput, LLMConfig, RecordTurnInput, WorkspaceConfig


def _state_workspace_for(code_workspace: Path) -> Path | None:
    """Root de ESTADO para este workspace, o None si no hay override.

    Slug determinista desde el path absoluto del workspace de código —
    único por agente, estable entre deploys.
    """
    state_root = (os.environ.get("EXOCLAW_STATE_DIR") or "").strip()
    if not state_root:
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(code_workspace.resolve())).strip("-")
    return Path(state_root) / slug


def _build_conversation(llm: LLMConfig, ws: WorkspaceConfig) -> DefaultConversation:
    provider = LiteLLMProvider(
        api_key=llm.api_key,
        api_base=llm.api_base,
        default_model=llm.model,
        extra_headers=llm.extra_headers or None,
    )
    code_workspace = Path(ws.path)
    state_workspace = _state_workspace_for(code_workspace)
    if state_workspace is None:
        return DefaultConversation.create(
            workspace=code_workspace,
            provider=provider,
            model=llm.model,
            memory_window=llm.memory_window,
        )

    # Construcción manual espejo de `DefaultConversation.create`, con el
    # HistoryStore apuntando al state dir persistente. Memory y prompts
    # quedan en el workspace de código (ver docstring del módulo).
    from exoclaw_conversation.context import ContextBuilder
    from exoclaw_conversation.memory import MemoryStore
    from exoclaw_conversation.session.manager import SessionManager

    memory = MemoryStore(code_workspace, provider, llm.model)
    return DefaultConversation(
        history=SessionManager(state_workspace),
        memory=memory,
        prompt=ContextBuilder(code_workspace, memory=memory),
        memory_window=llm.memory_window,
    )


@activity.defn
async def build_prompt(input: BuildPromptInput) -> list[dict[str, Any]]:
    """Build the full messages list for this turn (system prompt + history + user message)."""
    conv = _build_conversation(input.llm, input.workspace)
    result = await conv.build_prompt(
        input.session_id,
        input.message,
        channel=input.channel,
        chat_id=input.chat_id,
        media=input.media,
        plugin_context=input.plugin_context,
    )
    return result  # type: ignore[return-value]


@activity.defn
async def record_turn(input: RecordTurnInput) -> None:
    """Persist the new messages from this turn to the conversation store."""
    conv = _build_conversation(input.llm, input.workspace)
    await conv.record(input.session_id, input.new_messages)
