"""Helper compartido `run_agent_turn` para deduplicar `_run_turn` entre Sales y Remarketing.

# DETERMINISTIC: importado vía `workflow.unsafe.imports_passed_through()` desde
# cada workflow. Solo invoca `workflow.execute_activity` y manipula listas locales
# y dataclasses. Cero I/O directo, cero `time.time`, cero `random`.

Ademas extrae las **decisiones** que las tools emitan en su payload de respuesta:
las tools devuelven JSON con keys conocidas (`transfer_decision`,
`schedule_remarketing`); el helper las parsea a dataclasses (`TransferDecision`,
`ScheduleRemarketingDecision`) y las expone via `TurnResult` para que el workflow
ejecute la activity-dispatcher correspondiente (ADR-001).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.activities.conversation import build_prompt, record_turn
    from exoclaw_temporal.activities.llm import llm_chat
    from exoclaw_temporal.config import (
        BuildPromptInput,
        ExecuteToolInput,
        LLMChatInput,
        RecordTurnInput,
        SessionInput,
    )

    from src.platform.contracts import (
        EpisodeClosedDecision,
        EscalationDecision,
        OrderRegisteredDecision,
        ScheduleRemarketingDecision,
        TransferDecision,
    )
    from src.platform.llm_text_sanitizer import (
        is_no_message_abstention,
        looks_like_admin_leak,
        sanitize_llm_text,
    )
    from src.platform.temporal.activities import execute_tool
    from src.platform.temporal.retry_policies import (
        _CONV_OPTIONS,
        _LLM_OPTIONS,
        _TOOL_OPTIONS,
    )

    # HU-003 — atribución de costos: el baggage (session.id/episode.id/whatsapp.
    # number) se setea DENTRO de la activity `llm_chat` (Temporal NO propaga
    # baggage workflow→activity — verificado), pasándolo por LLMChatInput.baggage.
    # Acá solo derivamos los IDs y resolvemos el episodio activo.
    from src.platform.constants import WHATSAPP_SESSION_PREFIX
    from src.platform.observability.cost_attribution import (
        RecordEpisodeLLMUsageInput,
        get_active_episode_id_activity,
        record_episode_llm_usage_activity,
    )


# ----------------------------------------------------------------------
# L-3: registro mínimo para CUALQUIER worker cuyo workflow corra
# `run_agent_turn` — exactamente las activities que los helpers de este
# módulo invocan. Un worker que registre menos NO falla al boot: muere en
# runtime con NotFoundError en el primer turno real (caso eta:
# `record_episode_llm_usage` faltaba y la invocación vive detrás del gate
# `patched("episode-llm-cost-v1")` — la detonó la primera conversación
# real de un cliente con el agente). Los workers conversacionales deben
# spread-ear esta tupla en su `activities=[...]`, nunca listar estas a
# mano. Si agregás un `execute_activity` nuevo a los helpers, sumalo acá
# EN EL MISMO COMMIT.
# ----------------------------------------------------------------------
CONVERSATIONAL_TURN_ACTIVITIES: tuple = (
    build_prompt,
    llm_chat,
    execute_tool,
    record_turn,
    get_active_episode_id_activity,
    record_episode_llm_usage_activity,
)


@dataclass
class PendingMessage:
    """DTO compartido para el queue de mensajes pendientes en cada workflow.

    Replicado del shape de `PendingMessage` que vivia inline en cada workflow.

    `plugin_context` (PR-D, opcion a): hueco para **datos volatiles del turno**
    — A-MEM contextual, snippets retrieved, motivo de la inyeccion sintetica.
    **NO es para identidad ni catalogo**: esos viven en el workspace canonico
    del agente (`workspace/{IDENTITY,SOUL,USER,TOOLS,AGENTS}.md` y
    `workspace/skills/<name>/SKILL.md`) y los lee `ContextBuilder` dentro de
    `build_prompt`. El path Sales pasa siempre `None`. El path Remarketing
    aun usa este campo para forwardear el legacy `shared_brain/*.md` hasta
    que su workspace migre a la layout DEHA. Cuando esa migracion aterrice,
    `plugin_context` puede quedarse vacio para Remarketing tambien — o
    repurposearse para A-MEM.

    El campo sobrevive en la signature del signal (`send_message`) por
    compatibilidad de replay (R-JSON, fixture v2 en sales / v1 en remarketing).
    Cambiar la signature del signal implica fixture bump (v3) y posiblemente
    drain operativo (ADR-009).

    `is_handoff` (debounce/coalesce v1): marca un mensaje sintetico que NO
    debe entrar al rol "user" de la conversacion — en su lugar se mueve a
    `plugin_context` durante el coalesce. Lo usa el flujo Remarketing→Sales:
    el bootstrap (o el refresh per-iteration) lee `pending_handoff_summary`
    de metadata.json y seedea `_pending` con un marker. Default False
    mantiene replay-safety (mensajes pre-deploy deserializan al default).

    `is_ghost_trigger` (run 5f43bcd0): marca el trigger administrativo de
    ghosting que el workflow inyecta al detectar abandono. Un batch que lo
    contiene produce un turno ADMIN: su texto es reporte interno (etiqueta +
    resumen) y NUNCA debe enrutarse al canal del cliente. El tipo de turno
    es asi una propiedad ESTRUCTURAL del batch — no una confluencia de
    flags (`_force_shutdown`) que otra rama puede desarmar (el corrientazo
    de run 48ec6df5 limpiaba el flag y el resumen admin llego al cliente).
    Default False mantiene replay-safety.
    """
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None
    is_handoff: bool = False
    is_ghost_trigger: bool = False


@dataclass
class InboxMsg:
    """DTO de un inbound en la bandeja durable con watermark (PR burst-inbox).

    Espejo enriquecido de `PendingMessage`: agrega `seq` (secuencia monotónica
    — HOY asignada por el run loop del workflow al armar el batch, orden de
    `_pending` = orden de signals garantizado por Temporal; el incremento
    bandeja/watermark la moverá al signal handler), `wamid` (message_id de
    Meta, para reply-to / mark_as_read; hoy siempre None) y `ts_ms` (hoy 0).
    El invariante del watermark ("respondido ⇔ seq ≤ _acked_seq") vivirá
    sobre estos `seq`.
    """
    seq: int
    wamid: str | None
    text: str
    ts_ms: int
    media: list[str] | None = None
    plugin_context: list[str] | None = None
    is_handoff: bool = False


@dataclass
class TurnResult:
    """Resultado de un turno LLM-tool-LLM.

    Ademas del `final_content` y los `tools_used`, expone las decisiones que las
    tools dispararon (a interpretar por el workflow para llamar la activity
    dispatcher correspondiente, o terminar el workflow en el caso de
    escalation).
    """
    final_content: str
    tools_used: list[str] = field(default_factory=list)
    # Bug saludo descartado (run ddd0d472 / session-wa_573125671604): el texto
    # client-facing que el LLM emite JUNTO con una tool call (ej. "Buenos días.
    # Bienvenido a *Hubara*..." acompañando `send_quick_replies`) antes se perdía
    # — el loop solo capturaba `final_content` (el content del último mensaje SIN
    # tool_calls). Acá acumulamos, en orden, los content no vacíos de cada
    # iteración con tool_calls para que el workflow los envíe como burbujas antes
    # de `final_content`. Vacío en el caso normal (un solo mensaje sin tools).
    pre_tool_messages: list[str] = field(default_factory=list)
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None
    escalation_decision: EscalationDecision | None = None
    # HU-WA24H-001 Sprint 2: emitted by `ManageConversationTagTool` when a
    # CLOSING_TAG actually closes an active episode (idempotent — only set
    # when close_episode mutated state). The workflow lifts this into
    # `EpisodeClosedEvent` via the dispatcher to cancel any running watchdog.
    episode_closed_decision: EpisodeClosedDecision | None = None
    # Fix integridad orden↔tag: emitted by `RegisterOrderTool` when an order
    # registers successfully. Makes the registration VISIBLE to the workflow,
    # which runs `ensure_payment_pending_closure_activity` as a deterministic
    # safety net — guaranteeing the "pago pendiente" tag + escalation land
    # even if the LLM never emits the follow-up tool calls.
    order_registered_decision: OrderRegisteredDecision | None = None
    # Fase 1 interrupción ("corrientazo", run eda8d460): True cuando el turno
    # se abortó ANTES de tocar al cliente porque llegó un mensaje nuevo. El
    # caller debe recomponer el batch (viejo + pendientes) y relanzar el turno
    # — nada se envió, nada se registró en el historial (record_turn skipped).
    interrupted: bool = False
    # Saludo de primer contacto (runs dc32f7fe / 3ce50ef3):
    # True cuando el historial que vio el LLM no tenía NINGÚN mensaje del
    # agente — primer intercambio de la conversación. El caller (Sales) lo usa
    # para garantizar la burbuja de apertura cuando el turno salió por tool
    # (el content junto a tool calls se descarta por default-deny).
    first_contact: bool = False
    # Textos client-facing que viajaron en los params de tools outbound
    # (`intro_text`, `body`, ...): el caller detecta si el saludo ya salió por
    # el canal legítimo y no lo duplica.
    outbound_tool_texts: list[str] = field(default_factory=list)
    # Scorecard por etapa (HU-SC-0): cada tool ejecutada en el turno con sus
    # args y su resultado (acotado), en orden. Sin esto el evaluador solo veía
    # NOMBRES de tools y no podía distinguir un formulario de envío mandado de
    # uno rechazado por su guarda (PR #281). Lista en memoria del workflow: no
    # agrega commands a la history (replay-safe).
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    # Narración que el default-deny descartó (texto junto a tool calls). El
    # cliente NO la vio; el evaluador sí debe verla (el LLM quiso decir algo).
    discarded_narration: list[str] = field(default_factory=list)


# Tope del resultado de cada tool que viaja en `TurnResult.tool_events`: el
# resumen de la traza solo lee las claves de control del envelope (error,
# queued, degraded_from, count…), que van al inicio del JSON.
_TOOL_EVENT_RESULT_MAX = 4000


def _try_parse_decision_payload(raw: str) -> dict[str, Any] | None:
    """Intenta parsear el resultado JSON de una tool que emitio una decision.

    Las tools que emiten decisiones (routing.py, tags.py) devuelven JSON con un
    campo `transfer_decision` o `schedule_remarketing`. Si el parse falla o el
    campo no existe, retorna None: la respuesta es texto plano para el LLM.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ── Corte de turno por tools que esperan al cliente (run b730c006, L-11) ────
# El tool-loop deja al LLM encadenar tools hasta que él decida parar. Cuando
# una tool deja la conversación ESPERANDO input del cliente (picker de
# variantes, formulario de envío, confirmación de pedido, quick replies),
# seguir iterando es la receta del desastre: en el run b730c006 el modelo
# mandó el picker de colores y EN EL MISMO TURNO "respondió" por el cliente
# (set_order_slot color inventado), fijó cantidad y mandó el formulario de
# envío. El prompt ya lo prohibía (TOOLS.md: "tu siguiente mensaje SOLO después
# de que el cliente respondió") — el corte mecánico lo hace imposible.
TURN_ENDING_TOOLS: frozenset[str] = frozenset({
    "present_variant_picker",
    "request_shipping_details",
    "present_order_confirmation",
    "send_quick_replies",
})

# v2 (run eda8d460): el catálogo también deja la conversación esperando al
# cliente ("Escoge la que más te guste..."). Sin el corte, el LLM emitía OTRO
# texto en la iteración siguiente repitiendo el intro_text del catálogo → dos
# burbujas redundantes. Elegida por `workflow.patched("turn-ending-present-
# products-v1")` — v1 congelada para replay de histories deployadas.
TURN_ENDING_TOOLS_V2: frozenset[str] = TURN_ENDING_TOOLS | frozenset(
    {"present_products"}
)

# Regla del operador 2026-09-07: el mensaje estándar de tarifas de envío
# (`send_shipping_rates`) ES la respuesta — sin corte, el LLM emitía otra
# burbuja reformulando (o "corrigiendo") las tarifas. Tool NUEVA: ninguna
# history deployada la invocó, así que sumarla al set v2 no altera el replay
# (mismo criterio que agregar una tool al registry) — no requiere patch.
TURN_ENDING_TOOLS_V2 = TURN_ENDING_TOOLS_V2 | frozenset({"send_shipping_rates"})

# LEGACY — solo para replay de histories pre "no-pre-tool-forward-v1".
# La whitelist intentaba clasificar el content junto a tool calls POR EL
# BATCH ("junto a una tool presentacional es intro legítima") — pero el batch
# no determina la naturaleza del texto: en el run 1c9ef231 "Encontré 10 velas
# religiosas. Las muestro al cliente." salió como burbuja junto a
# present_products. El contrato vivo es default-deny (ver run_agent_turn): el
# content junto a tools NUNCA se forwardea; el texto del cliente viaja en los
# params de la tool (`intro_text`, `body`). Eliminar este set + la rama vieja
# al deprecar el patch (drain: idle 1min en Sales).
PRESENTATIONAL_TOOLS: frozenset[str] = TURN_ENDING_TOOLS | frozenset(
    {"present_products", "send_shipping_rates"}
)

# Prefijos de tools que tocan al cliente (envían o encolan un UI intent que el
# flush entregará). Una vez que un batch con alguna de estas corrió, el turno
# ya NO admite restart limpio (Fase 1 interrupción): el cliente vio (o verá)
# algo de este turno.
_OUTBOUND_TOOL_PREFIXES: tuple[str, ...] = ("present_", "send_", "request_")


def _ends_turn(tool_names: list[str], *, version: int = 1) -> bool:
    """¿Este batch de tool calls deja la conversación esperando al cliente?"""
    ending = TURN_ENDING_TOOLS_V2 if version >= 2 else TURN_ENDING_TOOLS
    return any(name in ending for name in tool_names)


def _rejected_by_tool(payload: dict[str, Any] | None) -> bool:
    """¿La tool declaró que NO le mostró nada al cliente (`queued: false`)?

    Contrato de las tools outbound (`ui_intents.py`): `queued: true` = el UI
    intent quedó encolado para el cliente; `queued: false` + `error` = se negó
    y el mensaje le dice al modelo qué hacer. Un resultado sin envelope
    ("ok", texto plano) NO cuenta como rechazo: ante la duda, se asume que
    salió (el corte conservador de L-11).
    """
    return isinstance(payload, dict) and payload.get("queued") is False


def _starts_outbound(tool_names: list[str]) -> bool:
    """¿Este batch produce contenido client-visible (send directo o UI intent)?"""
    return any(name.startswith(_OUTBOUND_TOOL_PREFIXES) for name in tool_names)


def _keeps_pre_tool_content(tool_names: list[str]) -> bool:
    """¿El content que acompaña este batch es una intro legítima (presentacional)?"""
    return any(name in PRESENTATIONAL_TOOLS for name in tool_names)


def coalesce_pending(pending: list[PendingMessage]) -> PendingMessage:
    """Combina N mensajes pendientes en un solo `PendingMessage` para un turno.

    Reglas:
      * Mensajes con `is_handoff=True` NO entran al rol "user" — su contenido se
        mueve a `plugin_context` como `[HANDOFF_REMARKETING]: ...`. Asi no
        contaminan la conversacion JSONL ni el system prompt con prompts
        sinteticos como `[SISTEMA INTERNO]: ...`.
      * Mensajes reales del cliente se concatenan con `"\n"` (preservando orden
        de llegada). Asi una rafaga "Hola si" + "Me recuerdas cuanto vale X?"
        se procesa como un solo turno con ambos mensajes en contexto.
      * Si NO hay mensajes reales pero si handoff, se usa el ultimo handoff como
        mensaje principal — caso: el cliente engaga Remarketing con "Hola", el
        dispatcher seedea handoff, pero ningun mensaje real llega antes del
        debounce (Sales saluda proactivamente).
      * `plugin_context` existente de cualquier msg se preserva (concatena).
      * `media` se concatena (raro tener media en handoff; preservar de user).

    El `PendingMessage` resultante tiene `is_handoff=False` (ya consumido).
    """
    user_msgs = [p for p in pending if not p.is_handoff]
    handoff_msgs = [p for p in pending if p.is_handoff]

    plugin_ctx: list[str] = []
    for h in handoff_msgs:
        if h.message:
            plugin_ctx.append(f"[HANDOFF_REMARKETING]: {h.message}")
    for p in pending:
        if p.plugin_context:
            plugin_ctx.extend(p.plugin_context)

    media_combined: list[str] = []
    for p in pending:
        if p.media:
            media_combined.extend(p.media)

    if user_msgs:
        combined = "\n".join(p.message for p in user_msgs if p.message)
    elif handoff_msgs:
        combined = _handoff_takeover_framing(handoff_msgs[-1].message)
    else:
        combined = ""

    return PendingMessage(
        message=combined,
        media=media_combined or None,
        plugin_context=plugin_ctx or None,
        is_handoff=False,
    )


def _handoff_takeover_framing(summary: str) -> str:
    """Framing del mensaje principal cuando el turno es SOLO un handoff.

    L-12 (run 3607aecc): el summary del handoff iba CRUDO al rol "user"
    ("Cliente respondió 'A sí' al recordatorio..."), indistinguible del
    trigger que ve Remarketing — el LLM de ventas patrón-matcheaba "debo
    transferir a ventas" y se autotransfería en vez de retomar la venta.
    El framing deja inequívoco quién es y qué debe hacer. Compartido por
    `coalesce_pending` y `coalesce_inbox` (era un literal duplicado — riesgo
    de drift). Tono en TUTEO colombiano (REGLA #1; guard
    `test_no_voseo_in_agent_strings.py` escanea este archivo).

    Incidente wa_573229041190 (2026-07-17, run 019f7234): el "retoma la
    venta EXACTAMENTE donde quedó" no tenía rama para cuando NO hay venta
    pendiente — el cliente ya había comprado y recibido dos pedidos, y el
    LLM resolvió la instrucción imposible re-presentando un producto ya
    comprado que nadie pidió. Ahora el framing distingue los dos casos.
    """
    return (
        "[SISTEMA — HANDOFF DE REMARKETING A VENTAS]: Eres el agente de "
        "ventas y el control de esta conversación YA ES TUYO; no existe "
        "ninguna transferencia pendiente y no debes anunciarla. Contexto "
        f"del handoff: {summary}\n"
        "Primero mira el historial y decide en cuál de estos DOS casos "
        "estás:\n"
        "1) Hay una venta A MEDIO CAMINO (producto, aroma, color, cantidad "
        "o datos de envío sin completar): retómala EXACTAMENTE donde quedó "
        "y pide SOLO el siguiente dato pendiente. NO re-preguntes nada ya "
        "elegido ni hagas preguntas vagas tipo '¿en qué estábamos?'. Si el "
        "handoff trae la respuesta del cliente (ej. una cantidad), "
        "procésala como si acabara de escribirla.\n"
        "2) NO hay venta pendiente (la última compra ya quedó registrada o "
        "entregada, o no había pedido en curso): NO presentes ni ofrezcas "
        "productos que el cliente no pidió. Responde a lo que el cliente "
        "escribió; si solo saludó, saluda breve y pregunta en qué puedes "
        "ayudar. Y si el handoff NO trae ningún mensaje del cliente que "
        "responder, responde EXACTAMENTE `NO_MESSAGE` (una sola palabra, "
        "sin explicación) — eso suprime el envío y el cliente no verá "
        "nada. NUNCA escribas tu decisión de no responder como texto.\n"
        "En ambos casos: no menciones sistemas ni procesos internos."
    )


def _build_burst_note(user_msgs: list[InboxMsg]) -> str:
    """Nota de turno determinista para una ráfaga (>1 mensaje del cliente).

    Le dice al LLM que responda al conjunto como un hilo — sin ignorar ninguno
    ni contestar solo el último (el patrón "el bot solo ve uno"). Va a
    `plugin_context`, no al rol user: es metadata del turno, no algo que el
    cliente escribió. Determinista (sale del inbox ordenado por `seq`).
    """
    listed = "\n".join(f'  {i}) "{m.text}"' for i, m in enumerate(user_msgs, 1))
    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        f"El cliente te escribió {len(user_msgs)} mensajes seguidos desde tu "
        "última respuesta. Responde al conjunto como un solo hilo coherente, "
        "sin ignorar ninguno ni contestar solo el último:\n" + listed
    )


def coalesce_inbox(batch: list[InboxMsg], *, version: int = 1) -> PendingMessage:
    """Combina una ráfaga de `InboxMsg` en un solo `PendingMessage` para un turno.

    Espejo de `coalesce_pending` con dos reglas nuevas:
      * Ordena por `seq` ascendente ANTES de concatenar (una ráfaga puede llegar
        desordenada; el orden canónico es el de secuencia asignada por el workflow).
      * Con >1 mensaje real del cliente, inyecta `_build_burst_note` como PRIMER
        elemento de `plugin_context` (conciencia de ráfaga → el LLM sigue el hilo).

    Preserva el manejo de `is_handoff` (L-12): los handoff van a `plugin_context`
    como `[HANDOFF_REMARKETING]: ...`, nunca al rol user; sin user msg pero con
    handoff, el mensaje principal lleva el framing de ventas inequívoco.

    `version` pin-ea el comportamiento por patch gate del workflow (R-DET/L-9;
    v1 ya corre en prod desde 2026-07-01, NO cambiarla):
      * 1 — "burst-note-v1": la nota cuenta/lista TODOS los mensajes del cliente
        (incluye solo-media como `""`) y el plugin_context repite duplicados.
      * 2 — "burst-note-v2": la nota cuenta/lista SOLO mensajes con texto (un
        solo-media no infla el conteo ni mete `""`), y el plugin_context dedupea
        duplicados exactos preservando orden de primera aparición (una ráfaga de
        N inbounds Sales traía N copias del bloque bogota-context).
    """
    ordered = sorted(batch, key=lambda m: m.seq)
    user_msgs = [m for m in ordered if not m.is_handoff]
    handoff_msgs = [m for m in ordered if m.is_handoff]

    plugin_ctx: list[str] = []
    noteworthy = [m for m in user_msgs if m.text] if version >= 2 else user_msgs
    if len(noteworthy) > 1:
        plugin_ctx.append(_build_burst_note(noteworthy))
    for h in handoff_msgs:
        if h.text:
            plugin_ctx.append(f"[HANDOFF_REMARKETING]: {h.text}")
    for m in ordered:
        if m.plugin_context:
            plugin_ctx.extend(m.plugin_context)
    if version >= 2:
        plugin_ctx = list(dict.fromkeys(plugin_ctx))

    media_combined: list[str] = []
    for m in ordered:
        if m.media:
            media_combined.extend(m.media)

    if user_msgs:
        combined = "\n".join(m.text for m in user_msgs if m.text)
    elif handoff_msgs:
        # Mismo framing que `coalesce_pending` (L-12): el summary crudo en el
        # rol user era indistinguible del trigger de remarketing → el LLM se
        # autotransfería. El framing deja inequívoco quién es y qué debe hacer.
        combined = _handoff_takeover_framing(handoff_msgs[-1].text)
    else:
        combined = ""

    return PendingMessage(
        message=combined,
        media=media_combined or None,
        plugin_context=plugin_ctx or None,
        is_handoff=False,
    )


async def run_agent_turn(
    session: SessionInput,
    msg: PendingMessage,
    fallback_plugin_context: list[str] | None = None,
    episode_id: str | None = None,
    has_new_input: Callable[[], bool] | None = None,
    fabricate_fallback_on_empty: bool = True,
    skip_record_when: Callable[[str], bool] | None = None,
    admin_turn: bool = False,
) -> TurnResult:
    """Wrapper de atribución de costos (HU-003) sobre `_run_agent_turn_impl`.

    `admin_turn` (run b06636a6): el caller ya sabe que NINGÚN texto de este
    turno va al cliente (cierre por ghosting: el trigger es administrativo).
    Con eso el loop (a) termina el turno apenas una tool declara su cierre
    (`tag_closure.ends_turn`) sin pedir "un mensaje más", y (b) no le hace
    recordar al LLM un texto que nunca salió. Default False = turno de cliente.

    `skip_record_when(final_content)` → True hace que el turno NO se grabe en
    el historial del LLM (`record_turn`). Lo usa Remarketing para las
    abstenciones (`NO_MESSAGE`): grabadas, cada una era precedente del
    siguiente intento y el prompt crecía 8k→27k tokens (runs
    `01a0b0da`…`01a0b586`). Solo aplica a turnos SIN tools (una tool ya tuvo
    efectos: ese turno sí pasó). El caller lo pasa detrás de su propio
    `workflow.patched` — acá cambia la secuencia de activities.

    Arma el `baggage` (`session.id` / `whatsapp.number` / `episode.id`) y lo pasa
    a `_run_agent_turn_impl`, que lo mete en `LLMChatInput.baggage`. La activity
    `llm_chat` lo attachea al contexto ANTES del LLM call → el `BaggageSpanProcessor`
    lo copia como atributos al span gen_ai de OpenLIT → habilita
    `GROUP BY session.id, episode.id` sobre `gen_ai.usage.cost` en SigNoz.

    **El baggage NO se setea acá (en el workflow)**: Temporal NO propaga OTel
    baggage workflow→activity (verificado) — por eso los IDs viajan por el input
    de la activity, donde sí funciona el contexto live.

    `episode_id` se resuelve per-turn vía `get_active_episode_id_activity`
    (platform; las lecturas compartidas cruzan por platform, no cross-agente),
    detrás de `workflow.patched("cost-attribution-episode-v1")` para ser
    replay-safe en los session workflows long-lived (histories previas al deploy
    atribuyen sólo por número). Un caller puede pasar `episode_id` explícito (tests).
    """
    if episode_id is None and workflow.patched("cost-attribution-episode-v1"):
        # Resolver el episodio activo per-turn (read-only). Gated: los session
        # workflows son long-lived; sin el gate, replay-ar turnos previos al
        # deploy chocaría con NondeterminismError al ver este execute_activity
        # nuevo. Las histories viejas toman el else → episode_id=None.
        raw_episode_id = await workflow.execute_activity(
            get_active_episode_id_activity,
            session.session_id,
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )
        episode_id = raw_episode_id or None

    sid = session.session_id
    num = (
        sid[len(WHATSAPP_SESSION_PREFIX):]
        if sid.startswith(WHATSAPP_SESSION_PREFIX)
        else sid
    )
    baggage: dict[str, str] = {"session.id": sid, "whatsapp.number": num}
    if episode_id:
        baggage["episode.id"] = episode_id
    return await _run_agent_turn_impl(
        session,
        msg,
        fallback_plugin_context,
        baggage=baggage,
        episode_id=episode_id,
        has_new_input=has_new_input,
        fabricate_fallback_on_empty=fabricate_fallback_on_empty,
        skip_record_when=skip_record_when,
        admin_turn=admin_turn,
    )


async def _run_agent_turn_impl(
    session: SessionInput,
    msg: PendingMessage,
    fallback_plugin_context: list[str] | None = None,
    baggage: dict[str, str] | None = None,
    episode_id: str | None = None,
    has_new_input: Callable[[], bool] | None = None,
    fabricate_fallback_on_empty: bool = True,
    skip_record_when: Callable[[str], bool] | None = None,
    admin_turn: bool = False,
) -> TurnResult:
    """Ejecuta un turno completo de LLM con tool-loop. Es invocado desde `@workflow.run`.

    `msg.plugin_context` y `fallback_plugin_context` cargan **datos volatiles
    del turno** (PR-D, opcion a): A-MEM, snippets retrieved, motivos. La
    identidad / catalogo del agente ya NO viaja por aqui — se lee desde
    `workspace/*.md` en `build_prompt` via `ContextBuilder`. PR-D global
    cleanup (ADR-2026-05-06-10): tanto Sales como Remarketing pasan siempre
    `None` a estos parametros (la migracion DEHA workspace de Remarketing
    elimino el `_brain_cache` y `load_remarketing_brain_activity`). El campo
    sobrevive en la signature como hueco para futuros datos volatiles.

    `fallback_plugin_context` se mantiene como parametro opcional por
    compatibilidad de signature (replay-safe). En el futuro podria
    repurposearse para A-MEM (long-term memory contextual del turno).
    """
    plugin_context = msg.plugin_context if msg.plugin_context else fallback_plugin_context

    messages: list[dict[str, Any]] = await workflow.execute_activity(
        build_prompt,
        BuildPromptInput(
            session_id=session.session_id,
            message=msg.message,
            channel=session.channel,
            chat_id=session.chat_id,
            llm=session.llm,
            workspace=session.workspace,
            media=msg.media,
            plugin_context=plugin_context,
        ),
        **_CONV_OPTIONS,  # type: ignore[arg-type]
    )
    # Off-by-one vs upstream (caso 573229041190, 2026-07-07): exoclaw `loop.py`
    # graba `all_msgs[len(initial) - 1:]` — el -1 INCLUYE el mensaje del usuario
    # en el historial durable. Sin él, el LLM nunca ve lo que el cliente dijo
    # en turnos pasados (solo sus propios mensajes + tool results; verificado
    # en el history real: 82 mensajes, user:1) y re-pregunta datos ya dados
    # ("Ya te los di"). Solo cambia el payload de `record_turn` → replay-safe.
    initial_len = len(messages)
    if messages and messages[-1].get("role") == "user":
        initial_len -= 1
    # Primer contacto = ningún mensaje del agente en el historial visible
    # (misma condición que el bloque de contexto de turno: "no hay ningún
    # intercambio previo"). Cómputo puro sobre datos ya en la history →
    # replay-safe sin gate.
    first_contact = not any(m.get("role") == "assistant" for m in messages)
    outbound_tool_texts: list[str] = []

    iteration = 0
    final_content: str | None = None
    tools_used: list[str] = []
    # Bug saludo descartado (run ddd0d472): textos client-facing emitidos JUNTO
    # con tool calls. Solo manipulación de lista en memoria → no agrega commands
    # al history (replay-safe sin gate; el gate vive en el workflow que decide
    # enviarlos).
    pre_tool_messages: list[str] = []
    tool_events: list[dict[str, Any]] = []
    discarded_narration: list[str] = []
    # L-11 (run b730c006): corte de turno en tools que esperan al cliente +
    # descarte del content que acompaña tools internas. Gated: ambos cambian la
    # cantidad de commands del turno (menos llm_chat/execute_tool; menos sends
    # del caller) — histories pre-deploy toman la rama vieja (L-9).
    turn_cut_v1 = workflow.patched("turn-ending-tools-v1")
    # Redundancia catálogo (run eda8d460): present_products también corta.
    # Gate propio: cambia el shape del turno (menos llm_chat) — v1 congelada
    # para replay de histories deployadas.
    ends_turn_version = (
        2 if workflow.patched("turn-ending-present-products-v1") else 1
    )
    # Fase 1 interrupción ("corrientazo", run eda8d460): el caller pasa
    # `has_new_input` (lectura determinista de su bandeja de signals). Gated:
    # el abort/supresión cambia la cantidad de commands. El short-circuit
    # evita grabar el marker en workflows cuyo caller no participa (ej.
    # remarketing pasa None).
    interrupts_enabled = has_new_input is not None and workflow.patched(
        "turn-interrupt-v1"
    )
    # True apenas un batch tocó al cliente (send directo o UI intent
    # encolado): desde ahí no hay restart limpio — solo supresión del cierre.
    outbound_started = False
    transfer_decision: TransferDecision | None = None
    schedule_remarketing: ScheduleRemarketingDecision | None = None
    escalation_decision: EscalationDecision | None = None
    escalation_farewell = ""
    episode_closed_decision: EpisodeClosedDecision | None = None
    order_registered_decision: OrderRegisteredDecision | None = None
    # Costo por episodio: acumula los tokens del turno sobre las N iteraciones del
    # tool-loop; se persiste al episodio tras el loop (record_episode_llm_usage).
    turn_prompt_tokens = 0
    turn_completion_tokens = 0

    while iteration < session.llm.max_iterations:
        iteration += 1

        response = await workflow.execute_activity(
            llm_chat,
            LLMChatInput(
                messages=messages,
                llm=session.llm,
                tool_definitions_json=session.tool_definitions_json,
                # HU-003: IDs de atribución de costo. `llm_chat` los attachea al
                # contexto → el span gen_ai de OpenLIT los recibe (Temporal no
                # propaga baggage workflow→activity, por eso van por el input).
                baggage=baggage,
            ),
            **_LLM_OPTIONS,  # type: ignore[arg-type]
        )
        if response.usage:
            turn_prompt_tokens += int(response.usage.get("prompt_tokens", 0) or 0)
            turn_completion_tokens += int(
                response.usage.get("completion_tokens", 0) or 0
            )

        # Checkpoint A — restart limpio (Fase 1, caso contra-entrega del run
        # eda8d460): el cliente escribió MIENTRAS el LLM pensaba y este turno
        # todavía no tocó al cliente (ni sends, ni UI intents, ni decisiones
        # capturadas). Abortamos ANTES de ejecutar el batch: el caller
        # recompone (batch viejo + pendientes) y relanza — el cliente recibe
        # UNA respuesta que considera todo, no dos cruzadas. No se registra
        # nada en el historial (record_turn skipped): el turno "nunca pasó".
        if (
            interrupts_enabled
            and not outbound_started
            and transfer_decision is None
            and schedule_remarketing is None
            and escalation_decision is None
            and episode_closed_decision is None
            and order_registered_decision is None
            and has_new_input()
        ):
            workflow.logger.info(
                "turno interrumpido pre-outbound (corrientazo): llegó mensaje "
                "nuevo del cliente; el caller recompone el batch y relanza"
            )
            return TurnResult(
                final_content="", tools_used=tools_used, interrupted=True
            )

        if response.has_tool_calls:
            batch_tool_names = [tc.name for tc in response.tool_calls]
            if _starts_outbound(batch_tool_names):
                outbound_started = True
            # Cierre que una tool de ESTE batch declaró en su envelope
            # (`tag_closure`, ver el corte más abajo). `None` = la tool no
            # mandó texto para el cliente; "" = mandó y nada era seguro.
            batch_tag_ends_turn = False
            batch_tag_customer_message: str | None = None
            # Una tool del batch devolvió `error`: el modelo debe leerlo.
            batch_tool_failed = False
            # Corte L-11 por RESULTADO (runs 01a0caec / 01a0cb16): ¿alguna tool
            # que espera al cliente le mostró algo de verdad en este batch?
            batch_awaits_customer = False
            # DEFAULT-DENY (run 1c9ef231): el content que acompaña una tool
            # call es narración interna SIEMPRE — se descarta y se loguea. El
            # texto para el cliente viaja en los params de la tool
            # (`intro_text`, `body`), que toda tool presentacional exige. La
            # whitelist vieja (PRESENTATIONAL_TOOLS) clasificaba el texto por
            # el batch de tools, pero el batch no determina la naturaleza del
            # texto: "Encontré 10 velas religiosas. Las muestro al cliente."
            # salió como burbuja junto a present_products. patched(): histories
            # en vuelo que SÍ forwardearon replayean con la rama whitelist;
            # tras el drain, eliminar la rama vieja + PRESENTATIONAL_TOOLS +
            # `workflow.deprecate_patch("no-pre-tool-forward-v1")`.
            if response.content:
                sanitized_pre = sanitize_llm_text(response.content)
                if sanitized_pre.text:
                    if workflow.patched("no-pre-tool-forward-v1"):
                        discarded_narration.append(sanitized_pre.text)
                        workflow.logger.info(
                            "pre-tool content descartado (default-deny: el texto "
                            "junto a tool calls nunca va al cliente; batch="
                            f"{batch_tool_names}): {sanitized_pre.text[:120]!r}"
                        )
                    elif not turn_cut_v1 or _keeps_pre_tool_content(batch_tool_names):
                        pre_tool_messages.append(sanitized_pre.text)
                    else:
                        workflow.logger.info(
                            "pre-tool content descartado (narración junto a tools "
                            f"internas {batch_tool_names}): {sanitized_pre.text[:120]!r}"
                        )
            messages = [*messages, response.to_assistant_message()]
            for tc in response.tool_calls:
                tools_used.append(tc.name)
                # Texto que SÍ llega al cliente vía params de tools outbound
                # (solo lista en memoria → no agrega commands, replay-safe).
                if tc.name.startswith(_OUTBOUND_TOOL_PREFIXES) and isinstance(
                    tc.arguments, dict
                ):
                    outbound_tool_texts.extend(
                        v for v in tc.arguments.values() if isinstance(v, str)
                    )
                result = await workflow.execute_activity(
                    execute_tool,
                    ExecuteToolInput(
                        name=tc.name,
                        params=tc.arguments,
                        session_id=session.session_id,
                        channel=session.channel,
                        chat_id=session.chat_id,
                        workspace=session.workspace,
                    ),
                    **_TOOL_OPTIONS,  # type: ignore[arg-type]
                )
                tool_events.append(
                    {
                        "name": tc.name,
                        "args": tc.arguments if isinstance(tc.arguments, dict) else {},
                        "result": result[:_TOOL_EVENT_RESULT_MAX]
                        if isinstance(result, str)
                        else "",
                    }
                )

                # Intentar extraer decisiones del payload JSON (ADR-001).
                payload = _try_parse_decision_payload(result)
                if not _rejected_by_tool(payload) and _ends_turn(
                    [tc.name], version=ends_turn_version
                ):
                    batch_awaits_customer = True
                if payload is not None:
                    if isinstance(payload.get("error"), str) and payload["error"]:
                        batch_tool_failed = True
                    if "transfer_decision" in payload and isinstance(payload["transfer_decision"], dict):
                        td = payload["transfer_decision"]
                        transfer_decision = TransferDecision(
                            session_id=str(td.get("session_id", session.session_id)),
                            target_route=str(td.get("target_route", "ventas")),
                            summary=td.get("summary"),
                        )
                    if "schedule_remarketing" in payload and isinstance(payload["schedule_remarketing"], dict):
                        sr = payload["schedule_remarketing"]
                        schedule_remarketing = ScheduleRemarketingDecision(
                            session_id=str(sr.get("session_id", session.session_id)),
                            motivo=str(sr.get("motivo", "")),
                            delay_seconds=int(sr.get("delay_seconds", 60)),
                        )
                    if "escalation_decision" in payload and isinstance(payload["escalation_decision"], dict):
                        ed = payload["escalation_decision"]
                        escalation_decision = EscalationDecision(
                            session_id=str(ed.get("session_id", session.session_id)),
                            reason_category=str(ed.get("reason_category", "OTHER")),
                            summary=str(ed.get("summary", "")),
                        )
                        # Despedida del relevo: texto FINAL que la tool ya
                        # validó (param `customer_message` del LLM o la
                        # despedida aprobada). Ver el corte de abajo.
                        farewell_raw = payload.get("customer_message")
                        escalation_farewell = (
                            farewell_raw if isinstance(farewell_raw, str) else ""
                        )
                    if "episode_closed" in payload and isinstance(payload["episode_closed"], dict):
                        # HU-WA24H-001 Sprint 2: parsing del envelope que
                        # `ManageConversationTagTool` emite cuando un
                        # CLOSING_TAG efectivamente cerró un episodio activo.
                        ec = payload["episode_closed"]
                        episode_id_raw = ec.get("episode_id")
                        if isinstance(episode_id_raw, str) and episode_id_raw:
                            episode_closed_decision = EpisodeClosedDecision(
                                session_id=str(
                                    ec.get("session_id", session.session_id)
                                ),
                                episode_id=episode_id_raw,
                                closing_tag=str(ec.get("closing_tag", "")),
                            )
                    if "order_registered" in payload and isinstance(payload["order_registered"], dict):
                        # Fix integridad orden↔tag: `RegisterOrderTool` emitió
                        # esto al cerrar una orden con éxito. Lo levantamos para
                        # que el workflow corra la red de seguridad de cierre.
                        orr = payload["order_registered"]
                        order_id_raw = orr.get("order_id")
                        if isinstance(order_id_raw, str) and order_id_raw:
                            order_registered_decision = OrderRegisteredDecision(
                                session_id=str(
                                    orr.get("session_id", session.session_id)
                                ),
                                order_id=order_id_raw,
                                payment_method=str(orr.get("payment_method", "")),
                                total_cop=int(orr.get("total_cop", 0)),
                                currency=str(orr.get("currency", "COP")),
                                motivo=str(orr.get("motivo", "")),
                                portavelas_included=bool(
                                    orr.get("portavelas_included", False)
                                ),
                            )
                    if "tag_closure" in payload and isinstance(payload["tag_closure"], dict):
                        # `ManageConversationTagTool` declara si su tag es
                        # AUTOSUFICIENTE (`ends_turn`) y, si el modelo lo
                        # mandó, el texto para el cliente YA validado. La
                        # decisión de cortar lee SOLO este resultado grabado:
                        # nada de regex acá (L-21). Se ASIGNA (no se acumula):
                        # con dos tags en un batch vale el último, igual que
                        # en metadata gana la última escritura.
                        closure = payload["tag_closure"]
                        batch_tag_ends_turn = closure.get("ends_turn") is True
                        closure_text = closure.get("customer_message")
                        batch_tag_customer_message = (
                            closure_text
                            if batch_tag_ends_turn and isinstance(closure_text, str)
                            else None
                        )

                messages = [
                    *messages,
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result},
                ]

            # La escalación TERMINA el turno (run 5ed9af2d, 2026-09-18). Antes
            # el loop pedía OTRO llm_chat tras el tool result ("…NO generes más
            # respuestas") y el modelo, obligado a emitir algo, le acusaba
            # recibo al sistema: "Listo, la conversación quedó en manos del
            # equipo humano." — y eso era el final_content que recibía el
            # cliente (la despedida buena había viajado como content junto a
            # la tool call → descartada por el default-deny). Ahora el texto
            # del cliente es el `customer_message` que la tool devuelve ya
            # validado, y NO hay llm_chat posterior: el acuse no se genera,
            # así que no hay nada que pueda filtrarse (misma idea que L-11: el
            # prompt no frena al modelo, el corte sí). Va ANTES del corte
            # L-11 para que un batch [tool que espera al cliente, escalate]
            # conserve la despedida como final_content (el UI intent igual
            # sale por el flush; sales_session exceptúa la supresión del
            # picker cuando hubo escalación).
            # Solo corta si la escalación OCURRIÓ (hay decision): una tool que
            # la rechazó (`escalated: false`) deja seguir al LLM.
            # patched(): histories pre-deploy replayean con el llm_chat extra;
            # tras el drain (idle 5min en Sales), eliminar la rama vieja +
            # `workflow.deprecate_patch("escalation-ends-turn-v1")`.
            if escalation_decision is not None and workflow.patched(
                "escalation-ends-turn-v1"
            ):
                final_content = sanitize_llm_text(escalation_farewell).text
                if final_content:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": final_content},
                    ]
                else:
                    workflow.logger.warning(
                        "escalación sin customer_message en el envelope: el "
                        "turno termina sin texto (no se le pide acuse al LLM)",
                        extra={"session_id": session.session_id},
                    )
                break

            # L-11: el batch dejó la conversación ESPERANDO al cliente (picker /
            # formulario / confirmación / quick replies) → el turno termina ACÁ.
            # Sin esto el LLM puede "responderse a sí mismo" (run b730c006:
            # mandó el picker de colores y en el mismo turno fijó un color que
            # el cliente nunca eligió + el formulario de envío). Se ejecuta el
            # batch COMPLETO antes de cortar (cada tool_call_id ya tiene su
            # result en `messages` — protocolo LLM intacto). `final_content=""`
            # explícito: el fallback "se me cortó un segundito" es para fallas
            # del modelo, no para este corte deliberado (el guard falsy del
            # caller no envía burbuja vacía).
            #
            # Corte por RESULTADO, no por nombre (runs 01a0caec / 01a0cb16,
            # 2026-09-22): la premisa del corte es que el cliente YA tiene algo
            # delante. Si TODAS las tools que esperan al cliente se negaron
            # (`queued: false`, "No se envió nada"), no hay nada que esperar:
            # cortar deja al bot callado y el modelo nunca lee el rechazo (que
            # trae el siguiente paso). Sigue iterando; si en la vuelta siguiente
            # una tool terminal SÍ sale, ahí corta. b730c006 queda cubierto: con
            # el selector mostrado se corta aunque otra tool del batch falle.
            # `patched()` va ÚLTIMO: solo se consulta cuando la regla nueva
            # difiere de la vieja (nada salió). Histories pre-deploy replayean
            # con el corte viejo; tras el drain (idle 5min en Sales), eliminar
            # la rama vieja + `deprecate_patch("turn-cut-on-delivery-v1")`.
            if turn_cut_v1 and _ends_turn(batch_tool_names, version=ends_turn_version):
                if batch_awaits_customer or not workflow.patched(
                    "turn-cut-on-delivery-v1"
                ):
                    workflow.logger.info(
                        f"turno cortado: {batch_tool_names} espera respuesta del cliente"
                    )
                    final_content = ""
                    break
                workflow.logger.info(
                    f"sin corte: {batch_tool_names} se negó y no mostró nada al "
                    "cliente; el modelo lee el rechazo"
                )

            # El tag AUTOSUFICIENTE termina el turno (run b06636a6, 2026-09-18).
            # Misma clase que la escalación de arriba (L-20), otra tool: tras
            # `manage_conversation_tag` el loop pedía OTRO llm_chat y el modelo
            # acusaba recibo ("Etiqueta registrada."). En el cierre por
            # ghosting no salía (turno admin) pero quedaba en el historial como
            # few-shot del acuse y costaba ~55K prompt tokens por cierre; en un
            # turno de cliente era el final_content. La tool DECLARA el cierre
            # (`tag_closure.ends_turn`; los tags combo que exigen
            # `escalate_to_human` NO lo declaran: ahí el modelo aún tiene
            # trabajo) y el turno termina cuando ya no hay nada que pedirle:
            #   * turno admin → sin texto (el cliente no está);
            #   * turno de cliente CON `customer_message` → ese texto TAL CUAL
            #     lo devolvió la tool, que ya lo validó ("" = habló y nada era
            #     seguro: silencio, no se le reabre el canal). Sin sanitizar
            #     acá: el workflow solo LEE lo grabado (L-21).
            # Turno de cliente SIN `customer_message` → NO corta: el cliente
            # sigue esperando respuesta y ese llm_chat es legítimo.
            # Va DESPUÉS del corte L-11 (al revés que la escalación): en un
            # batch contradictorio [picker, tag + despedida] gana el picker, que
            # es lo que pasaba antes de este corte — si no, la despedida
            # quedaba suprimida por `suppress_text_for_picker` pero persistida
            # al dashboard y al historial sin haber salido.
            # Si ALGUNA tool del batch falló, tampoco corta: el modelo tiene
            # que ver el error (dos tags y el último rebota → sobrevivía el
            # cierre del primero).
            # `patched()` va ÚLTIMO a propósito: solo se consulta (y graba su
            # marker) cuando el corte aplicaría. Histories pre-deploy replayean
            # con el llm_chat extra; tras el drain (idle 5min en Sales),
            # eliminar la rama vieja + `deprecate_patch("tag-ends-turn-v1")`.
            if (
                batch_tag_ends_turn
                and not batch_tool_failed
                and (admin_turn or batch_tag_customer_message is not None)
                and workflow.patched("tag-ends-turn-v1")
            ):
                final_content = "" if admin_turn else (batch_tag_customer_message or "")
                if final_content:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": final_content},
                    ]
                elif not admin_turn:
                    # Corte SILENCIOSO en turno de cliente: que quede rastro
                    # (el análogo de la escalación también avisa).
                    workflow.logger.warning(
                        "cierre de tag con `customer_message` vacío: la tool "
                        "descartó todo lo que mandó el modelo; el turno termina "
                        "sin texto (no se le pide otro mensaje al LLM)",
                        extra={"session_id": session.session_id},
                    )
                workflow.logger.info(
                    f"turno terminado por cierre de tag ({batch_tool_names}; "
                    f"admin={admin_turn}, con_texto={bool(final_content)})"
                )
                break
        else:
            # ── Empty-content recovery (post-mortem run df5a8fe2-bb7c-4627-b861-dc19643467be) ──
            # DeepSeek v4 (and other thinking-mode models) occasionally finishes
            # with `content=""` while putting the user-facing answer in
            # `reasoning_content`. With the previous code path, the empty content
            # propagated to `send_whatsapp_message_activity` which has a falsy
            # guard `if result.final_content` → message silenced, client
            # perceives the bot as "stuck thinking". After 60s the ghosting
            # timer fires and remarketing kicks in prematurely.
            #
            # Recovery: append a workflow-side nudge as a `system` turn and
            # retry ONE iteration of the LLM loop. We do NOT promote
            # `reasoning_content` directly to the client — reasoning channels
            # may contain meta-commentary ("Debo llamar la tool X...") which
            # would break the agent's human persona. If the retry still ends
            # empty, surface a natural human fallback that keeps the
            # conversation moving without sounding scripted.
            if (
                not response.content
                and response.reasoning_content
                and iteration < session.llm.max_iterations
            ):
                workflow.logger.warning(
                    "LLM returned empty content with reasoning_content present — "
                    "nudging model to emit the final answer in `content`",
                    extra={
                        "session_id": session.session_id,
                        "reasoning_len": len(response.reasoning_content),
                        "finish_reason": response.finish_reason,
                        "iteration": iteration,
                    },
                )
                # Inject a workflow-side nudge as a system-role reminder.
                # We do NOT echo reasoning_content into the prompt — that
                # could prime the model to repeat the same failure pattern.
                messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "Tu mensaje al cliente debe ir en el campo `content`, "
                            "no en el canal de razonamiento. Responde ahora al "
                            "cliente directamente, en español natural y cálido, "
                            "sin meta-comentarios sobre tu proceso de pensamiento."
                        ),
                    },
                ]
                # Continue the loop — next iteration retries llm_chat.
                continue

            # Sanitize: strip meta-prefijos ('Here's my attempt:'),
            # comillas envolventes, y duplicación back-to-back. Bug
            # 579d34e7 — el LLM emitió ambos defectos a la vez en
            # remarketing. Defensa última antes de send_*.
            raw_content = response.content or ""
            sanitized = sanitize_llm_text(raw_content)
            if sanitized.changed:
                workflow.logger.warning(
                    f"LLM output sanitized: actions={sanitized.actions}",
                    extra={
                        "session_id": session.session_id,
                        "actions": list(sanitized.actions),
                        "raw_len": len(raw_content),
                        "clean_len": len(sanitized.text),
                    },
                )
            final_content = sanitized.text
            # Final empty-content fallback: model failed to recover even after
            # the nudge (or we hit max_iterations). Use a HUMAN-sounding
            # natural recovery line that mirrors what a real seller would say
            # when something hiccups. Critical: do NOT mention "bot", "system",
            # "error", "AI" — preserve the human persona.
            if not final_content:
                # Premortem A4 (run 5f43bcd0): la disculpa fabricada solo tiene
                # sentido en un turno REACTIVO (el cliente espera respuesta).
                # En turnos proactivos/administrativos (gancho de remarketing,
                # cierre de ghosting) el caller pasa
                # `fabricate_fallback_on_empty=False`: vacío = abstención — un
                # cliente frío no debe recibir "¿Me repites lo que
                # necesitabas?" de la nada.
                if fabricate_fallback_on_empty:
                    workflow.logger.error(
                        "LLM final response remained empty after recovery attempt — "
                        "using human-sounding fallback line",
                        extra={
                            "session_id": session.session_id,
                            "reasoning_len": (
                                len(response.reasoning_content)
                                if response.reasoning_content
                                else 0
                            ),
                            "iteration": iteration,
                        },
                    )
                    final_content = "¡Perdón! Justo se me cortó un segundito. ¿Me repites lo que necesitabas?"
                else:
                    workflow.logger.info(
                        "LLM final response vacío en turno proactivo — se "
                        "trata como abstención (sin fallback fabricado)"
                    )

            # Checkpoint B — supresión del cierre stale (Fase 1, caso "Solo
            # plegaria de luz" del run eda8d460): el turno YA tocó al cliente
            # (cards/quick-replies en vuelo) — no hay restart limpio — pero el
            # cliente escribió mientras el LLM componía este texto de cierre.
            # Enviarlo se cruzaría con lo que acaba de decir (el bot "no
            # escucha"). Lo suprimimos y NO lo registramos en el historial
            # (el LLM no debe recordar haber dicho algo que nunca salió); el
            # mensaje nuevo se procesa en el turno siguiente con los tool
            # results de este turno ya en memoria.
            if interrupts_enabled and outbound_started and has_new_input():
                workflow.logger.info(
                    "cierre de turno suprimido: el cliente escribió mientras "
                    "el LLM componía el texto final (se procesa en el "
                    "siguiente turno)"
                )
                final_content = ""
                break

            msg_dict: dict[str, Any] = {"role": "assistant", "content": final_content}
            if response.reasoning_content is not None:
                msg_dict["reasoning_content"] = response.reasoning_content
            if response.thinking_blocks:
                msg_dict["thinking_blocks"] = response.thinking_blocks
            messages = [*messages, msg_dict]
            break

    if final_content is None:
        final_content = (
            "¡Perdón! Justo se me cortó un segundito. ¿Me repites lo que necesitabas?"
            if fabricate_fallback_on_empty
            else ""
        )

    # Lo que NO salió, el LLM no lo recuerda (runs b06636a6 → 5ed9af2d).
    # `record_turn` corre ACÁ, antes de que el caller decida enviar: el acuse
    # "Etiqueta registrada." de un cierre por ghosting nunca se envió pero sí se
    # persistió como `assistant`, y la sesión siguiente lo leyó como few-shot
    # de "tras una tool administrativa se responde con un parte de estado".
    # Se recorta el texto final cuando ya se sabe que no llega al cliente:
    #   * turno admin (lo dice el caller), o
    #   * huele a parte interno — mismo veredicto que el tripwire del choke
    #     point `send_whatsapp_message_activity`, que tampoco lo dejaría salir.
    # El resto del turno (mensaje del cliente, tool calls, tool results) queda:
    # pasó de verdad. `NO_MESSAGE` NO se recorta a propósito (el detector lo
    # caza como token interno, por eso la excepción explícita): no es un acuse
    # sino el canal CORRECTO de abstención — verlo usado es el few-shot bueno;
    # borrarlo empuja a declinar en prosa, que es justo lo que se filtraba.
    # Un turno que termina en `tool` ya es forma de prod (corte L-11), y
    # exoclaw descarta los assistant vacíos al persistir.
    # SIN patch gate: solo cambia el PAYLOAD de `record_turn`, no la secuencia
    # de commands (L-9 versiona forma, no contenido); en replay la activity no
    # se re-ejecuta. Por eso `extended` va en su default: nada que versionar.
    recorded = messages[initial_len:]
    never_reaches_customer = admin_turn or (
        looks_like_admin_leak(final_content)
        and not is_no_message_abstention(final_content)
    )
    if (
        final_content
        and never_reaches_customer
        and recorded
        and recorded[-1].get("role") == "assistant"
        and not recorded[-1].get("tool_calls")
        and recorded[-1].get("content") == final_content
    ):
        workflow.logger.info(
            "texto final no recordado en el historial del LLM (nunca llega "
            f"al cliente; admin={admin_turn}): {final_content[:120]!r}"
        )
        recorded = recorded[:-1]
    # Turno admin SIN ninguna tool call: no dejó ningún hecho que recordar, y
    # lo único que quedaría es el trigger de sistema SIN cerrar ("[SISTEMA]… NO
    # generes ninguna respuesta visible… SOLO llama la herramienta"). La sesión
    # siguiente lo leería pegado al mensaje nuevo del cliente y podría
    # aplicarle esa orden vieja (cliente que vuelve y recibe silencio). Antes
    # lo "cerraba" la prosa que ahora se recorta. El turno nunca pasó → lista
    # vacía; `record_turn` se agenda igual (misma forma de history). Con tool
    # calls el trigger SÍ queda: lo cierra la llamada, y el tag es un hecho.
    if admin_turn and not any(m.get("tool_calls") for m in recorded):
        recorded = []

    skip_record = (
        skip_record_when is not None
        and not tools_used
        and skip_record_when(final_content)
    )
    if not skip_record:
        await workflow.execute_activity(
            record_turn,
            RecordTurnInput(
                session_id=session.session_id,
                new_messages=recorded,
                llm=session.llm,
                workspace=session.workspace,
            ),
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )

    # Persistir el costo LLM del turno al episodio (dato de negocio → frontend).
    # Gated por replay-safety (session workflows long-lived; histories viejas
    # toman el else → no se agrega el execute_activity nuevo). Solo si hay episodio
    # resuelto y se consumieron tokens. La activity es idempotente (activity_id).
    if (
        episode_id
        and (turn_prompt_tokens or turn_completion_tokens)
        and workflow.patched("episode-llm-cost-v1")
    ):
        await workflow.execute_activity(
            record_episode_llm_usage_activity,
            RecordEpisodeLLMUsageInput(
                session_id=session.session_id,
                episode_id=episode_id,
                prompt_tokens=turn_prompt_tokens,
                completion_tokens=turn_completion_tokens,
                model=session.llm.model,
            ),
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )

    return TurnResult(
        final_content=final_content,
        tools_used=tools_used,
        pre_tool_messages=pre_tool_messages,
        transfer_decision=transfer_decision,
        schedule_remarketing=schedule_remarketing,
        escalation_decision=escalation_decision,
        episode_closed_decision=episode_closed_decision,
        order_registered_decision=order_registered_decision,
        first_contact=first_contact,
        outbound_tool_texts=outbound_tool_texts,
        tool_events=tool_events,
        discarded_narration=discarded_narration,
    )
