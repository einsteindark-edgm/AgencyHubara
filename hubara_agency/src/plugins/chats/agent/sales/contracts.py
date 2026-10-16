"""Boundary DTOs del dominio Sales.

Aplicacion de R-JSON: cualquier valor que cruce `workflow.execute_workflow` /
`workflow.run` es un dataclass plano JSON-serializable.

`SalesSessionInput` es el input minimo del `HubaraSalesSessionWorkflow`. La
construccion del `SessionInput` (LLMConfig + WorkspaceConfig + ToolRegistry +
tool_definitions_json) ocurre dentro del workflow via
`bootstrap_sales_session_activity` (PR F7), no en los callers, para alinearse
con el patron ya aplicado a Remarketing (PR F6.1).

`turn_count` viaja por el DTO para que el `continue_as_new` (cada 50 turnos)
pueda preservar el contador entre runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SalesSessionInput:
    """Input del `HubaraSalesSessionWorkflow`.

    Campos:
      * `session_id`: identificador de la conversacion (`{WHATSAPP_SESSION_PREFIX}{phone}`).
      * `turn_count`: contador de turnos preservado a traves de
        `continue_as_new` (default 0 para nuevas sesiones).
      * `runtime_workspace_path`: ruta del workspace canonico del agente
        (donde viven `IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`,
        `AGENTS.md`, `memory/*` y `skills/*`). Resuelto en el composition root
        via `config/env.py:get_workspace_path()` y propagado como string para
        cumplir R-JSON. PR-B: `bootstrap_sales_session_activity` lo consume
        para `WorkspaceConfig(path=...)` y falla fast si esta vacio. PR-D:
        `shared_brain/` ya no existe en Sales — esta ruta es la unica fuente
        de identidad / tono / catalogo del agente.

    El primer mensaje del usuario NO viaja por este DTO: el caller arranca el
    workflow y luego dispara `signal(send_message, ...)` con el texto. Asi el
    seed entra por la cola de signals como cualquier otro mensaje.
    """

    session_id: str
    turn_count: int = 0
    runtime_workspace_path: str | None = None


@dataclass(frozen=True)
class ReplyWindow:
    """Lo que el turno de ventas lee mientras espera a que vuelva el LLM
    (incidente 2026-10-15; `read_reply_window`).

    Campos:
      * `route`: quién tiene el chat (`active_route`; sin ruta, `ventas`). Si lo
        tomó una persona (`humano`), el turno deja de esperar.
      * `window_expires_at_ms`: cuándo cierra la ventana de 24 h para el texto
        libre (`service_window_expires_at_ms`); None si no se sabe.
    """

    route: str
    window_expires_at_ms: int | None = None


@dataclass(frozen=True)
class InboundMessageInput:
    """Input del `InboundMessageWorkflow` (entrada durable, ADR 2026-10-15).

    Campos:
      * `message`: el `WhatsAppMessage` como dict (`dataclasses.asdict`), tal
        como lo dejó el webhook: parseado y con la dirección de su conversación
        (`_in_their_conversation`). Lleva `raw` (el mensaje crudo de Meta).
      * `after`: `wamid` del mensaje anterior del MISMO cliente en el mismo
        POST. El workflow espera a que ese termine antes de ingerir este (el
        orden que antes daban los background tasks en serie).
      * `received_at_ms`: cuándo llegó al webhook (para medir la espera en
        Temporal; la fase 2 lo usa como reloj del ingest).
    """

    message: dict[str, Any]
    after: str | None = None
    received_at_ms: int = 0


def inbound_workflow_id(wamid: str) -> str:
    """Id del workflow de entrada de un mensaje: con `REJECT_DUPLICATE`, una
    re-entrega de Meta del mismo `wamid` no entra dos veces."""
    return f"inbound-{wamid}"
