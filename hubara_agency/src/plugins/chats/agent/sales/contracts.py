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
      * `pending_topics`, `perception_mode`, `perception_profile`,
        `photos_reading`: lo que el run lleva del turno y el continue-as-new
        le pasa al siguiente (auditoría de Temporal, 2026-10-15): el asunto que
        la verificación dejó pendiente, el modo y el perfil del clasificador y
        las fotos que el ingest sigue leyendo. Una sesión nueva no los trae.
      * `handoff_wamids`: los mensajes del cliente que ya entraron con un
        traspaso de remarketing (V2): si también llegan por señal después del
        continue-as-new, no se contestan dos veces.
      * `undelivered`: los avisos para el turno siguiente de respuestas que no
        se confirmaron (V2, `workflows/delivery_v2.py`).

    El primer mensaje del usuario NO viaja por este DTO: el caller arranca el
    workflow y luego dispara `signal(send_message, ...)` con el texto. Asi el
    seed entra por la cola de signals como cualquier otro mensaje.
    """

    session_id: str
    turn_count: int = 0
    runtime_workspace_path: str | None = None
    pending_topics: list[str] | None = None
    perception_mode: str | None = None
    perception_profile: str | None = None
    photos_reading: list[str] | None = None
    handoff_wamids: list[str] | None = None
    undelivered: list[str] | None = None


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
class HandoffReply:
    """Lo que el cliente le escribió a remarketing, para el turno de Ventas
    que sigue al traspaso (caso 2026-10-16; `read_handoff_reply`).

    Ventas solo recibía el resumen de remarketing: arrancó sin el «Si» del
    cliente, sin lo que dijo un colega y sin el gancho, y se calló.

    Campos:
      * `text`: el mensaje del turno — lo que escribió el cliente, tal cual,
        con la nota de lo que pasó en el chat después del último mensaje de
        Ventas (colega, avisos, gancho) adelante, si hubo algo.
      * `customer_text`: solo lo que escribió el cliente (traza, clasificador).
      * `wamids`: los mensajes del cliente que el turno ya trae (si alguno
        también llega por señal, no se contesta dos veces).
      * `ts_ms`: cuándo llegó el último; None si no se sabe.
    """

    text: str
    customer_text: str
    wamids: list[str]
    ts_ms: int | None = None
