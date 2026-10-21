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

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class InboundMessageInput:
    """Input del `InboundMessageWorkflow` (entrada durable, ADR 2026-10-15).

    Campos:
      * `message`: el `WhatsAppMessage` como dict (`dataclasses.asdict`), tal
        como lo dejó el webhook: parseado y con la dirección de su conversación
        (`_in_their_conversation`), SIN el `raw` de Meta: el ingest no lo lee
        y la historia de Temporal Cloud guarda el input 30 días, en claro.
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


# --- Los pasos del ingest (entrada durable, fase 2) -------------------------
# `InboundMessageWorkflow` corre cada paso del ingest en su propia activity. Lo
# que un paso le pasa al siguiente queda en el historial del workflow: un
# reintento de un paso no repite los anteriores. Datos chicos; el documento de
# la sesión se queda en el vault y cada paso lo relee.

#: Desenlaces del paso del turno (`IngestTurn.outcome`).
TURN_DELIVER = "deliver"  # historial + entrega a la sesión
TURN_HISTORY_ONLY = "history_only"  # al chat, sin turno (llegó fuera de ventana, acuse de despedida)
TURN_IGNORED = "ignored"  # sin texto efectivo: nada que escribir
TURN_TRANSCRIBE = "transcribe"  # nota de voz: primero la transcripción
TURN_DESCRIBE = "describe"  # foto: primero la visión


@dataclass(frozen=True)
class IngestRequest:
    """Un mensaje del cliente a lo largo de los pasos del ingest.

    Campos:
      * `message`: el `WhatsAppMessage` como dict, tal como lo dejó el webhook.
      * `received_at_ms`: llegada al webhook, el reloj de «llegó tarde» y
        «fuera de ventana» (0 = la hora del paso).
      * `persisted_image_url`, `photo_note`, `from_photo`: la reentrada de una
        foto ya leída (`<wamid>_vision`).
      * `customer_text` / `customer_text_from_message`: lo que escribió el
        cliente para las lecturas; la descripción de una foto no es suya.
    """

    message: dict[str, Any]
    received_at_ms: int = 0
    persisted_image_url: str | None = None
    customer_text: str | None = None
    customer_text_from_message: bool = True
    photo_note: str | None = None
    from_photo: bool = False


@dataclass(frozen=True)
class IngestState:
    """Paso 1, el estado de la sesión con sus lecturas: lo que decidió y ya
    escribió en el vault (episodio, ventana, lecturas) y lo que los pasos
    siguientes necesitan de esa decisión."""

    session_id: str
    now_ms: int
    sent_ms: int
    arrival_ms: int
    arrived_after_window: bool = False
    closing_ack: bool = False
    referral_already_seen: bool = False
    campaign_reply_note: str | None = None
    campaign_reply_touch: dict[str, Any] | None = None
    episode_boundary_note: str | None = None
    #: El resumen del episodio que este mensaje cerró (cómo cerró, productos,
    #: cupón): solo eso viaja; los datos de envío del borrador no salen del vault.
    previous_episode_summary: str | None = None
    #: Lo que las lecturas dicen del ÚLTIMO mensaje (señal de compra, pausa por
    #: aplazamiento, cortesía; `decisions.readings.last_message_marks`): lo
    #: escribe el paso del historial, en el orden de llegada.
    last_message_marks: dict[str, Any] | None = None
    #: La escritura del estado falló (no la saltó una lectura fallida): la
    #: activity lo reintenta.
    write_failed: bool = False


@dataclass(frozen=True)
class IngestTurn:
    """Paso 2, el carrito y el cupón: el texto del mensaje y el turno ya
    armado, o por qué no hay turno todavía (`outcome`)."""

    outcome: str
    text: str = ""
    turn_message: str = ""
    history: dict[str, Any] = field(default_factory=dict)
    is_flow_reply: bool = False
    structured_payload: dict[str, Any] | None = None
    inbound_meta: dict[str, Any] = field(default_factory=dict)
    prefer_sales: bool = False
    campaign_reply_note: str | None = None
    coupon_talk: bool = False
    cart_note: str | None = None
    gap_note: str | None = None
    reply_to: dict[str, Any] | None = None


# --- El orden de entrega por cliente (ADR 2026-10-16) ----------------------


@dataclass(frozen=True)
class InboundArrival:
    """Un mensaje que se anuncia al `InboundSessionWorkflow` de su cliente
    para saber cuál es su anterior (`workflow_id` del suyo, llegada al webhook)."""

    workflow_id: str
    received_at_ms: int


@dataclass(frozen=True)
class InboundSessionState:
    """Lo que el `InboundSessionWorkflow` de un cliente lleva a su próximo run
    (continue-as-new): los últimos anunciados."""

    recent: list[InboundArrival] = field(default_factory=list)


def inbound_session_workflow_id(session_id: str) -> str:
    """Id del `InboundSessionWorkflow` de un cliente: `inbound-wa_<número>`.
    No choca con el de un mensaje (`inbound-wamid.…`)."""
    return f"inbound-{session_id}"
