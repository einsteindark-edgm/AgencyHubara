"""Override de `build_prompt` para el worker Sales: guion POR ETAPA del funnel.

Dieta de prompt (análisis runs eda8d460/019f24bf): el guion conversacional
completo (~19.6 KB) viajaba entero en CADA llamada al LLM, diluyendo la
atención sobre las reglas de la etapa actual (las 28 llamadas de una sola
conversación real sumaron 1.06M prompt tokens). Este override:

  1. Lee `metadata.json` de la sesión (I/O permitido: es una activity).
  2. Resuelve la etapa del funnel de forma DETERMINISTA con
     `resolve_funnel_stage` (proyección pura del `order_draft` del episodio
     activo — la etapa NO la elige el LLM).
  3. Pasa `skills=[<etapa>]` al `DefaultConversation.build_prompt` → el
     ContextBuilder inyecta SOLO el guion de esa etapa como Active Skill
     (además del core `sales_script`, que es `always:true`).

Registro: `@activity.defn(name="build_prompt")` — el MISMO nombre que la
activity genérica de exoclaw. El workflow no cambia (mismo command, mismos
args) → cero implicación de replay, sin patch gate. El worker Sales registra
ESTA en lugar de la genérica (ver `workers/sales.py`); Remarketing sigue con
la genérica (el guion por etapa es específico de ventas).

DEHA: R-STATELESS (sin cache module-level), R-JSON (BuildPromptInput /
list[dict] — el mismo contrato de la genérica), R-DIP (no importa
temporalio.client ni workflows).
"""
from __future__ import annotations

from typing import Any

from temporalio import activity

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import BuildPromptInput

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.funnel_stage import (
    resolve_funnel_stage,
)


@activity.defn(name="build_prompt")
async def sales_build_prompt(input: BuildPromptInput) -> list[dict[str, Any]]:
    """`build_prompt` con guion por etapa (determinista desde metadata)."""
    # Import local (mismo patrón que flush_ui_intents): el valor se resuelve
    # al CALL time, así el fixture `_isolate_vault_dir` de tests puede
    # re-bindear el módulo config sin que este módulo capture el path viejo.
    from src.platform.config import WORKSPACE_VAULT_DIR

    metadata = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(input.session_id)
    stage = resolve_funnel_stage(metadata)

    conv = _build_conversation(input.llm, input.workspace)
    return await conv.build_prompt(  # type: ignore[return-value]
        input.session_id,
        input.message,
        channel=input.channel,
        chat_id=input.chat_id,
        media=input.media,
        plugin_context=input.plugin_context,
        skills=[stage],
    )
