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

Los prompts/skills (ContextBuilder) siguen leyéndose del workspace de
CÓDIGO: solo el historial se muda. Env var ausente → sessions en el
workspace (dev/tests).

SIN MEMORIA COMPARTIDA (incidente 2026-09-17, run 7889b9f6):
exoclaw_conversation, por default, consolida toda sesión con ≥ memory_window
mensajes: un LLM la resume en `<workspace>/memory/MEMORY.md` — UN archivo por
agente, compartido por todos los clientes — y ese archivo entra en la sección
`# Memory` del system prompt de TODAS las conversaciones. En prod el prompt de
un cliente llevó el pedido pendiente de otra clienta. Por eso toda
conversación se construye con `_NeverConsolidate` (el LLM de resumen nunca se
invoca, nada se escribe) + `_NoSharedMemory` (nada de MEMORY.md llega al
prompt). El contexto de cada cliente es SOLO su propio historial (ventana de
`memory_window` mensajes) + el plugin_context del turno.

SIN LA IDENTIDAD GENÉRICA DE EXOCLAW (run 28a8e407, 2026-09-23):
`ContextBuilder._get_identity` abre todo system prompt con "You are exoclaw,
a helpful AI assistant", rutas de MEMORY.md, guías de edición de archivos y
"State intent before tool calls" — le pedía al modelo narrar antes de cada
tool. Con thinking apagado esa narración vive en su texto y terminaba en el
mensaje al cliente. `_AgentContextBuilder` la reemplaza por un encabezado
neutro: quién es y cómo habla cada agente lo dicen SUS archivos.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from exoclaw_conversation.context import ContextBuilder
from exoclaw_conversation.conversation import DefaultConversation
from exoclaw_conversation.session.manager import Session, SessionManager
from temporalio import activity

from exoclaw_temporal.config import BuildPromptInput, LLMConfig, RecordTurnInput, WorkspaceConfig

log = logging.getLogger(__name__)


def _state_workspace_for(code_workspace: Path) -> Path | None:
    """Root de ESTADO para este workspace, o None si no hay override.

    Slug determinista desde el path absoluto del workspace de código —
    único por agente, estable entre deploys. OJO: mover/renombrar el
    workspace cambia el slug (el historial viejo queda en disco bajo el
    slug anterior — migración manual si importa).
    """
    state_root_raw = (os.environ.get("EXOCLAW_STATE_DIR") or "").strip()
    if not state_root_raw:
        return None
    state_root = Path(state_root_raw)
    # Guard anti-amnesia-silenciosa (premortem PR #183 §4.8): si el PADRE
    # del state root no existe, casi seguro el volumen persistente NO está
    # montado en este container — ensure_dir crearía el path en el fs
    # efímero y el historial volvería a morir con cada deploy, sin señal.
    if not state_root.exists() and not state_root.parent.exists():
        log.warning(
            "EXOCLAW_STATE_DIR=%s: ni el dir ni su padre existen — ¿falta el "
            "mount del volumen persistente en este container? El historial se "
            "escribirá igual, pero puede ser EFÍMERO.",
            state_root_raw,
        )
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(code_workspace.resolve())).strip("-")
    return state_root / slug


class _NeverConsolidate:
    """ConsolidationPolicy que nunca consolida: la historia de un cliente no
    se resume a ningún lado (ni se llama al LLM de resumen)."""

    async def should_consolidate(self, session: Session, *, memory_window: int) -> bool:
        return False

    async def consolidate(
        self, session: Session, *, archive_all: bool = False, memory_window: int = 50
    ) -> bool:
        return True


class _NoSharedMemory:
    """MemoryBackend vacío: el system prompt no lleva `# Memory` aunque
    `MEMORY.md` exista, y nada se escribe ahí."""

    def get_memory_context(self) -> str:
        return ""

    async def consolidate(self, session: Session, *args: Any, **kwargs: Any) -> bool:
        return True

    async def consolidate_messages(self, session: Session, **kwargs: Any) -> bool:
        return True


class _AgentContextBuilder(ContextBuilder):
    """ContextBuilder sin la identidad genérica de exoclaw (ver docstring del
    módulo): el prompt arranca con los archivos del agente."""

    def _get_identity(self) -> str:
        return (
            "# Agente de Hubara\n\n"
            "Tus instrucciones, tu forma de hablar y tus herramientas están en "
            "las secciones siguientes."
        )


def _build_conversation(llm: LLMConfig, ws: WorkspaceConfig) -> DefaultConversation:
    code_workspace = Path(ws.path)
    state_workspace = _state_workspace_for(code_workspace) or code_workspace
    memory = _NoSharedMemory()
    return DefaultConversation(
        history=SessionManager(state_workspace),
        memory=memory,
        prompt=_AgentContextBuilder(code_workspace, memory=memory),
        memory_window=llm.memory_window,
        consolidation_policy=_NeverConsolidate(),
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
