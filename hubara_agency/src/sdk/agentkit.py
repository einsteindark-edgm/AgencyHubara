"""AgentKit — todo lo que un worker conversacional necesita del SDK.

Cubre el turn-loop compartido, el registro de tools por extensión y las
factories de configuración LLM/workspace. Las lecciones de producción están
horneadas en la mecánica (no en prosa):

- ``CONVERSATIONAL_TURN_ACTIVITIES``: los workers SPREAD-ean esta tupla en su
  ``activities=[...]`` — JAMÁS listan a mano las activities del helper
  (lección L-3: registro incompleto = workflow FAILED en runtime). El guard
  ``test_conversational_activities_parity`` lo exige por AST.
- ``TURN_ENDING_TOOLS``: las tools de UI que dejan la conversación esperando
  al cliente CORTAN el loop (lección L-11 — el prompt explica, el código
  garantiza). Toda tool nueva ``present_*``/formulario entra a este set.
- ``register_tool_extension``: late-binding de tools por dominio. La clase
  referenciada en el lambda DEBE estar importada al top del worker (gotcha
  #6 — ``ruff check --select F821`` lo caza).

Uso canónico (en un worker)::

    from src.sdk.agentkit import (
        CONVERSATIONAL_TURN_ACTIVITIES,
        build_default_llm_config,
        register_tool_extension,
    )

    worker = Worker(
        client,
        task_queue=get_task_queue("chats", "sales"),
        activities=[*CONVERSATIONAL_TURN_ACTIVITIES, my_extra_activity],
        ...
    )
"""
from __future__ import annotations

from src.platform.registries import (
    build_default_llm_config as build_default_llm_config,
    build_workspace_config as build_workspace_config,
    get_base_tools_json as get_base_tools_json,
    get_base_tools_registry as get_base_tools_registry,
)
from src.platform.tool_extensions import (
    apply_tool_extensions as apply_tool_extensions,
    clear_tool_extensions as clear_tool_extensions,
    register_tool_extension as register_tool_extension,
)

# Canal de abstención de un turno proactivo (incidente wa_573229041190, run
# 019f7234): el prompt ofrece el sentinel NO_MESSAGE y el workflow lo detecta
# con este helper — sin él, "decidir no enviar" terminaba enviado al cliente
# como deliberación. Puro, stdlib-only (importable en workflow sandbox).
from src.platform.llm_text_sanitizer import (
    NO_MESSAGE_SENTINEL as NO_MESSAGE_SENTINEL,
    is_no_message_abstention as is_no_message_abstention,
)
from src.platform.workflow_helpers import (
    CONVERSATIONAL_TURN_ACTIVITIES as CONVERSATIONAL_TURN_ACTIVITIES,
    PRESENTATIONAL_TOOLS as PRESENTATIONAL_TOOLS,
    TURN_ENDING_TOOLS as TURN_ENDING_TOOLS,
    PendingMessage as PendingMessage,
    TurnResult as TurnResult,
    coalesce_pending as coalesce_pending,
    run_agent_turn as run_agent_turn,
)
