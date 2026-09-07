"""Override de `build_prompt` para el worker Sales: guion POR ETAPA del funnel.

Dieta de prompt (análisis runs eda8d460/019f24bf): el guion conversacional
completo (~19.6 KB) viajaba entero en CADA llamada al LLM, diluyendo la
atención sobre las reglas de la etapa actual (las 28 llamadas de una sola
conversación real sumaron 1.06M prompt tokens). Este override:

  1. Lee `metadata.json` de la sesión (I/O permitido: es una activity).
  2. Captura determinista de la cantidad en respuestas compuestas
     (`quantity_capture`, incidente run 943e6bff: "Una que colores tienes?"
     tras "¿Cuántas unidades deseas?" — el LLM atendió la pregunta y soltó el
     dato). Si el agente acaba de preguntar la cantidad y el cliente arranca
     con una, se persiste en el `order_draft` ANTES de armar el prompt.
  3. Resuelve la etapa del funnel de forma DETERMINISTA con
     `resolve_funnel_stage` (proyección pura del `order_draft` del episodio
     activo — la etapa NO la elige el LLM), sobre el draft ya actualizado.
  4. Refresca el bloque `[DATOS DEL PEDIDO YA CONFIRMADOS]` del
     `plugin_context` desde el draft persistido (el que armó el ingest puede
     estar desactualizado tras la captura del paso 2; también cubre el retry
     de la activity, que ya no captura pero sí debe proyectar).
  5. Pasa `skills=[<etapa>]` al `DefaultConversation.build_prompt` → el
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

import time
from typing import Any

from temporalio import activity

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import BuildPromptInput

# P-28: los plugins importan la fachada `src.sdk`, no `src.platform` directo.
from src.sdk.runtime import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.funnel_stage import (
    resolve_funnel_stage,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
    get_projectable_draft,
)
from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
    agent_asked_quantity,
    capture_quantity_from_reply,
    last_visible_agent_text,
    parse_leading_quantity,
)

_DRAFT_NOTE_PREFIX = "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE"


def _refresh_order_draft_note(
    plugin_context: list[str] | None, metadata: dict[str, Any]
) -> list[str] | None:
    """Reemplaza (o agrega) la nota del draft con la proyección FRESCA.

    Sin draft proyectable devuelve el contexto tal cual (la nota del ingest,
    si vino, se respeta: mismo gate, mismo resultado).
    """
    slots = get_projectable_draft(metadata)
    if not slots:
        return plugin_context
    fresh = build_order_draft_note(slots)
    kept = [c for c in (plugin_context or []) if not c.startswith(_DRAFT_NOTE_PREFIX)]
    return [*kept, fresh]


@activity.defn(name="build_prompt")
async def sales_build_prompt(input: BuildPromptInput) -> list[dict[str, Any]]:
    """`build_prompt` con guion por etapa (determinista desde metadata)."""
    # Import local (mismo patrón que flush_ui_intents): el valor se resuelve
    # al CALL time, así el fixture `_isolate_vault_dir` de tests puede
    # re-bindear el módulo (`src.sdk.runtime` está en la lista de módulos
    # vault-capturing del conftest) sin que este capture el path viejo.
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = store.read(input.session_id)

    conv = _build_conversation(input.llm, input.workspace)

    # --- Captura determinista de cantidad (respuesta compuesta) ---
    session = conv.history.get_or_create(input.session_id)
    last_agent_text = last_visible_agent_text(
        session.get_history(max_messages=input.llm.memory_window)
    )
    now_ms = int(time.time() * 1000)
    captured: int | None = None

    def _mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal captured
        captured = capture_quantity_from_reply(
            fresh,
            last_agent_text=last_agent_text,
            inbound_text=input.message,
            now_ms=now_ms,
        )
        return fresh if captured is not None else None

    # Pre-check PURO (texto del agente + texto del cliente) antes de tocar el
    # vault: `update` crea el dir de sesión + sidecar `.lock` aunque aborte, y
    # en el turno común (sin cantidad) no queremos ese side-effect. Cuando
    # aplica, `update` relee FRESCO bajo lock y solo escribe si el mutator
    # captura (None = abort sin write) — atómico frente a dashboard/ingest.
    written = (
        store.update(input.session_id, _mutator)
        if agent_asked_quantity(last_agent_text)
        and parse_leading_quantity(input.message) is not None
        else None
    )
    if written is not None:
        metadata = written
        activity.logger.info(
            "build_prompt: cantidad capturada del mensaje compuesto "
            "session=%s cantidad=%s",
            input.session_id,
            captured,
        )

    stage = resolve_funnel_stage(metadata)
    plugin_context = _refresh_order_draft_note(input.plugin_context, metadata)

    return await conv.build_prompt(  # type: ignore[return-value]
        input.session_id,
        input.message,
        channel=input.channel,
        chat_id=input.chat_id,
        media=input.media,
        plugin_context=plugin_context,
        skills=[stage],
    )
