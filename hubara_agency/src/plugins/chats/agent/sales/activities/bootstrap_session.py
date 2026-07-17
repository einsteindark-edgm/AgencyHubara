"""Activities especificas del dominio Sales.

Aqui viven los "puentes" entre el workflow y los prompts puros (``prompts.py``).
El proposito es mantener los workflows finos (driving adapters) y permitir que
los prompts evolucionen sin tocar la shape de history (la activity stub se
mantiene).

PR-E: este archivo se movio de ``activities.py`` (top-level) a
``activities/bootstrap_session.py`` (sub-folder) para alinear con el patron
de un modulo por activity. El re-export en ``activities/__init__.py``
preserva el import path publico.
"""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from exoclaw_temporal.config import SessionInput, WorkspaceConfig

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.registries import (
    build_default_llm_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.platform.state import FilesystemMetadataStore
from src.platform.tool_extensions import apply_tool_extensions
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.prompts import build_ghosting_prompt


@activity.defn(name="decide_ghosting_action")
async def decide_ghosting_action() -> str:
    """Devuelve el prompt inyectado cuando se detecta ghosting.

    No tiene side effects ni I/O. Existe como activity (no como llamada directa
    en el workflow) para mantener los strings de negocio fuera del workflow code.
    """
    return build_ghosting_prompt()


@activity.defn(name="bootstrap_sales_session_activity")
async def bootstrap_sales_session_activity(input: SalesSessionInput) -> SessionInput:
    """Construye el `SessionInput` JSON-safe del agente de Sales.

    Saca del @workflow.run la I/O de filesystem (`build_workspace_config` hace
    `Path.mkdir`) y la construccion del registry de tools. Replica el patron
    aplicado a Remarketing (PR F6.1) — los callers (service.py, dispatcher
    activities) ya no construyen `SessionInput` antes de `start_workflow`,
    solo pasan `SalesSessionInput(session_id=..., runtime_workspace_path=...)`
    y el bootstrap se ejecuta como primera activity dentro del workflow
    (R-DET / R-JSON).

    Input: `SalesSessionInput` plano (R-JSON). Pasamos el dataclass completo
    (en lugar de `session_id: str`) para no romper el shape de la activity al
    agregar campos en futuras iteraciones — solo se anaden campos al DTO con
    defaults.

    `input.runtime_workspace_path` (PR-B): el path canonico del workspace del
    agente de Sales (donde viven IDENTITY.md, SOUL.md, USER.md, TOOLS.md,
    AGENTS.md, memory/* y skills/*). **A partir de PR-B este path es el que
    `SessionInput.workspace` reporta**, asi que `ContextBuilder.build_system_prompt`
    (en `build_prompt` activity) lee identidad/tono/catalogo desde el workspace
    canonico — NO mas desde `shared_brain/*.md` via `plugin_context`. El vault
    per-session (`WORKSPACE_VAULT_DIR / session_id`) sigue siendo la home de
    `MessageHistoryStore` y `MetadataStore` (JSONL de la conversacion,
    metadata.json), pero esos los maneja el filesystem adapter, no este
    workspace.

    Es responsabilidad del composition root (PR-A) cablear el path:
    `composition.py` lee `EXOCLAW_WORKSPACE_SALES` via `config/env.py` e
    instancia `WorkspaceConfig(path=...)`, propagado como string en
    `SalesSessionInput.runtime_workspace_path` (R-JSON).
    """
    session_id = input.session_id
    llm = build_default_llm_config()

    # PR-B: el workspace que ve el runtime es el canonico del agente.
    # Sin path nadie cabledo el composition root correctamente — failfast
    # antes de llegar al `build_prompt` y emitir un system prompt vacio.
    #
    # ADR-2026-05-20 (Level 3 orchestration): el dispatcher genérico
    # (`src.platform.orchestration.dispatcher.dispatch_event_activity`) NO
    # sabe del path del worker target (sería R-DIP #10 violation conocer el
    # config de un sibling agent). Cuando el caller cross-worker es el
    # dispatcher declarativo, `runtime_workspace_path` viene en None y este
    # bootstrap lo resuelve localmente via `get_workspace_path()` — config
    # del worker propio, no leak cross-agent.
    runtime_path = input.runtime_workspace_path
    if runtime_path is None:
        # Local fallback: leemos el path del config del propio agente sales.
        # Esto es config local — NO cruza fronteras agent.
        from src.plugins.chats.agent.sales.config.env import get_workspace_path
        runtime_path = str(get_workspace_path())
        activity.logger.info(
            "bootstrap_sales_session_activity: runtime_workspace_path missing "
            "on input (declarative dispatcher or test fixture) — falling back "
            "to local config: %s",
            runtime_path,
        )

    ws = WorkspaceConfig(path=runtime_path)
    activity.logger.info(
        "bootstrap_sales_session_activity: workspace=%s",
        runtime_path,
    )

    # El registry de tools sigue apuntando al workspace canonico tambien;
    # PR-C revisara si las tools deben usar otro path (ej: per-session vault
    # para escrituras durables). Por ahora alineado con `ws.path`.
    registry = get_base_tools_registry(Path(ws.path))

    # POST-MORTEM workflow session-wa_573125671604: el bootstrap NO aplicaba
    # las extensions registradas en `worker.py` (`register_tool_extension`).
    # Resultado: `tool_definitions_json` viajaba VACIO al LLM, asi que el LLM
    # no veia ni `manage_conversation_tag` ni `transfer_to_sales_agent`. Antes
    # de Opcion A esto quedaba oculto porque `read_file`/`write_file` venian
    # en el base registry — el LLM "tenia algo" pero abusaba para bypass-ear
    # las tools de dominio. Con base vacio + sin extensions, el LLM no veia
    # nada. Fix: aplicar las extensions ANTES de generar el JSON, igual que
    # `execute_tool` lo hace al despachar (platform/temporal/activities.py:33).
    apply_tool_extensions(registry, Path(ws.path))

    return SessionInput(
        session_id=session_id,
        channel="whatsapp",
        chat_id=session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )


# ---------------------------------------------------------------------------
# Dynamic idle timeout (sesión c4e3416f)
#
# El timeout de ghosting "normal" es 60s (1 minuto sin que el cliente
# responda nada). Pero cuando acabamos de enviar un WhatsApp Flow nativo
# de datos de envío, llenar 5 campos en el formulario toma típicamente
# 1-3 minutos para un cliente real. A los 60s, el workflow Sales declaraba
# ghosting prematuramente, disparaba `SalesSessionCompletionEvent` →
# arrancaba Remarketing → `claim_conversation_routing(REMARKETING)` cambiaba
# `metadata.active_route` a "remarketing". Cuando el `nfm_reply` finalmente
# llegaba, se ruteaba a Remarketing — que no tiene tools de venta ni
# contexto del pedido. Cliente quedaba atascado.
#
# Fix: cuando hay un Flow real recientemente enviado (flag
# `shipping_flow_awaiting_reply_since_ms` escrito por
# `flush_pending_ui_intents_activity`), extender el timeout a hasta
# 10 minutos (tiempo restante desde el envío). Suficiente para que un
# cliente normal complete el formulario.
# ---------------------------------------------------------------------------

# Cap absoluto del timeout extendido. Si el cliente NO completa el Flow en
# 10 min, ghosting vuelve a su comportamiento normal (cierra sesión, dispara
# remarketing). 10 min es 4x el peor caso típico para un form de 5 campos.
_FLOW_PENDING_TIMEOUT_S = 10 * 60

# Default ghosting timeout — alineado con `_IDLE_TIMEOUT = timedelta(minutes=1)`
# del sales_session.py. Si cambia uno, cambiar el otro.
_DEFAULT_IDLE_TIMEOUT_S = 60


@activity.defn(name="read_idle_timeout_seconds")
async def read_idle_timeout_seconds_activity(session_id: str) -> int:
    """Devuelve el timeout (en segundos) que el workflow Sales debe usar para
    `workflow.wait_condition` antes de declarar ghosting.

    Política:
      * 60s default (lo normal — cliente no respondió en ese tiempo).
      * Si hay un `shipping_flow_awaiting_reply_since_ms` reciente
        (< 10 min), devolver el tiempo restante hasta llegar al cap de 10 min.
        El cliente está llenando el Flow nativo, no es ghosting real.
      * Si el flag está > 10 min, se considera vencido → 60s normal. La flag
        NO se limpia acá (eso lo hace el ingest al procesar el nfm_reply,
        para que el siguiente loop iter del workflow vuelva a 60s sin
        re-llamar la activity).

    R-DET safe: I/O en activity, no en workflow. El workflow llama esta
    activity ANTES del `wait_condition` y usa el valor devuelto como timeout
    determinístico para ese turno.
    """
    import json
    import time

    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return _DEFAULT_IDLE_TIMEOUT_S
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_IDLE_TIMEOUT_S

    sent_at_ms = data.get("shipping_flow_awaiting_reply_since_ms")
    if not isinstance(sent_at_ms, int):
        return _DEFAULT_IDLE_TIMEOUT_S

    elapsed_s = (time.time() * 1000 - sent_at_ms) / 1000.0
    if elapsed_s < 0 or elapsed_s >= _FLOW_PENDING_TIMEOUT_S:
        # Flag inválido (timestamp futuro) o vencido — default normal.
        return _DEFAULT_IDLE_TIMEOUT_S

    # Tiempo restante hasta el cap. Mínimo 60s para no devolver timeouts
    # absurdamente cortos si llegamos al filo de los 10 min.
    remaining_s = int(_FLOW_PENDING_TIMEOUT_S - elapsed_s)
    return max(_DEFAULT_IDLE_TIMEOUT_S, remaining_s)


@activity.defn(name="read_and_clear_pending_handoff")
async def read_and_clear_pending_handoff_activity(session_id: str) -> str | None:
    """Lee y limpia atomicamente `metadata.pending_handoff_summary`.

    Es el canal por el que el dispatcher Remarketing→Sales pasa el contexto
    del handoff al workflow Sales sin contaminar la conversacion JSONL con un
    mensaje sintetico `[SISTEMA INTERNO]: ...` (Fix 3 del PR de
    debounce/coalesce).

    Returns:
        El summary del handoff si estaba presente, o None si no hay handoff
        pendiente. La activity limpia el field del metadata atomicamente (read,
        pop, write) — la siguiente llamada devolvera None hasta que el
        dispatcher escriba otro handoff.

    Replay-safety: side-effect deterministico fuera del workflow (es activity).
    El workflow lo invoca via `workflow.execute_activity`; en replay Temporal
    re-aplica el resultado guardado en history sin re-ejecutar la activity.
    """
    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    data = store.read(session_id)
    summary = data.pop("pending_handoff_summary", None)
    if summary is not None:
        store.write(session_id, data)
        activity.logger.info(
            "read_and_clear_pending_handoff: handoff consumido session=%s",
            session_id,
        )
    return summary if summary else None


@activity.defn(name="read_order_draft_note")
async def read_order_draft_note_activity(session_id: str) -> str | None:
    """Nota `[DATOS DEL PEDIDO YA CONFIRMADOS]` para el turno de HANDOFF.

    Incidente 2026-07-17 (run 019f6db3): la inyección del draft al prompt
    solo existía en el path del webhook (`ingest_inbound_message`). El turno
    de handoff Remarketing→Sales arrancaba SIN el bloque aunque el draft
    estuviera intacto en el vault — el LLM, ciego y con la orden de "retomar
    donde quedó", exploró a ciegas y sobrescribió las notas del pedido
    (destruyendo los signos elegidos) antes de descubrir el draft.

    Mismo gate que el path del webhook (`get_projectable_draft`): episodio
    activo, sin order_id, slots no vacíos → note; si no, None.
    """
    from src.plugins.chats.agent.sales.use_cases.order_draft import (
        build_order_draft_note,
        get_projectable_draft,
    )

    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = store.read(session_id)
    slots = get_projectable_draft(metadata)
    if not slots:
        return None
    activity.logger.info(
        "read_order_draft_note: draft proyectado al handoff session=%s slots=%s",
        session_id,
        sorted(slots.keys()),
    )
    return build_order_draft_note(slots)
