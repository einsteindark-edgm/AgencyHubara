"""Workflow-level test del coalesce + debounce + handoff (Fixes 1, 3, 5).

Reproduce el escenario del bug `b9639be1` y verifica que con los fixes:
  * Dos signals consecutivos se coalescen en UN solo turno LLM y UN solo
    send_whatsapp.
  * El handoff `pending_handoff_summary` en metadata se consume y se mueve
    a `plugin_context` (no contamina el rol "user").
  * El typing indicator se dispara antes del LLM.

Usa `WorkflowEnvironment.start_time_skipping()` para simular timeouts de
debounce de forma determinista — los signals llegan antes que el
`wait_condition` resuelva, el time-skip avanza el clock 1.5s y la ventana
de silencio expira.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import (
    BuildPromptInput,
    ExecuteToolInput,
    LLMChatInput,
    LLMConfig,
    LLMResponseData,
    RecordTurnInput,
    SessionInput,
    ToolCallData,
    WorkspaceConfig,
)

from src.platform.contracts import PaymentPendingClosureResult
from src.platform.plugin_manifest import get_task_queue
from src.platform.llm_history_reset import ResetLLMHistoryInput
from src.platform.observability.cost_attribution import RecordEpisodeLLMUsageInput
from src.plugins.chats.agent.sales.contracts import SalesSessionInput

SALES_QUEUE = get_task_queue("chats", "sales")
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow


# --- Fake activities con contadores ----------------------------------------


class Tracker:
    """Estado mutable compartido entre los fakes y el test."""

    def __init__(self) -> None:
        self.build_prompt_calls: list[BuildPromptInput] = []
        self.llm_calls: int = 0
        self.send_whatsapp_calls: list[tuple[str, str]] = []
        self.typing_calls: list[str] = []
        self.persist_calls: list[tuple[str, str]] = []
        self.record_turn_calls: int = 0
        self.record_turn_new_messages: list[list[dict]] = []
        self.ghosting_calls: int = 0
        # Auditoría CAPI 2026-09-08: sesiones cuyo outbox flusheó el workflow.
        self.capi_flush_calls: list[str] = []
        self.start_sales_calls: int = 0
        self.execute_tool_calls: list[str] = []
        self.flush_calls: int = 0
        self.ensure_closure_calls: list[tuple[str, str, str]] = []
        self.closing_escalation_calls: list[tuple[str, str, str]] = []
        # Orden client-visible del turno: "send:<texto>" y "flush" en el orden
        # en que el workflow los ejecutó (el flush entrega los UI intents, ej.
        # el menú de present_products).
        self.timeline: list[str] = []
        self.first_contact_greeting_calls: int = 0
        self.variant_guard_calls: list[str] = []
        # HU-SC-0: payloads de la traza por turno, en orden.
        self.turn_traces: list[dict] = []
        # Runs edbb0d8b / 8e73b7dc: pedidos de corte del historial del LLM
        # (workspace de cada llamada), con su lugar en `timeline`.
        self.history_reset_calls: list[str] = []


# Burbuja 1 del guion de apertura (etapa_descubrimiento) — lo que la activity
# `build_first_contact_greeting` devuelve de noche en Bogotá.
FIRST_CONTACT_GREETING = (
    "¡Buenas noches! Bienvenido a *Hubara*, velas artesanales hechas a base "
    "de cera de palma, a mano en Colombia."
)


def _make_fake_activities(
    tracker: Tracker,
    *,
    workspace_path: str,
    prior_history: list[dict] | None = None,
    pending_handoff: str | None = None,
    handoff_sequence: list[str | None] | None = None,
    llm_responses: list[LLMResponseData] | None = None,
    tool_results: dict[str, str] | None = None,
    llm_call_hooks: dict[int, object] | None = None,
    ghosting_call_hooks: dict[int, object] | None = None,
    order_draft_note: str | None = None,
    payment_closure_result: PaymentPendingClosureResult | None = None,
    closing_escalation_result: bool = False,
    variant_guard_result: bool = False,
    send_returns_none: bool = False,
    flush_results: list[dict] | None = None,
):
    """Crea las activities fakes con `tracker` cerrado en closure.

    Devuelve la lista completa de activities a registrar en el Worker.

    `handoff_sequence` (L-12): valores que `read_and_clear_pending_handoff`
    devuelve call-por-call (agotada → None). Permite simular un handoff que
    se escribe DESPUÉS del bootstrap (mensaje del cliente durante la ventana
    de transferencia). `pending_handoff` es el shorthand legacy de una
    secuencia de un solo elemento.
    `tool_results` (L-12): resultado de `execute_tool` por nombre de tool
    (default "ok") — para simular tools que emiten decision payloads.
    """

    @activity.defn(name="bootstrap_sales_session_activity")
    async def fake_bootstrap(input: SalesSessionInput) -> SessionInput:
        return SessionInput(
            session_id=input.session_id,
            channel="whatsapp",
            chat_id=input.session_id,
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path=workspace_path),
            tool_definitions_json="[]",
        )

    # Estado: secuencia de handoffs call-por-call (agotada → None). El
    # shorthand `pending_handoff` equivale a una secuencia de 1 (one-shot).
    if handoff_sequence is None:
        handoff_sequence = [pending_handoff] if pending_handoff is not None else []
    _handoff_state = {"queue": list(handoff_sequence)}

    @activity.defn(name="read_and_clear_pending_handoff")
    async def fake_read_handoff(session_id: str) -> str | None:
        if _handoff_state["queue"]:
            return _handoff_state["queue"].pop(0)
        return None

    @activity.defn(name="read_order_draft_note")
    async def fake_read_order_draft_note(session_id: str) -> str | None:
        return order_draft_note

    @activity.defn(name="read_idle_timeout_seconds")
    async def fake_read_idle_timeout(session_id: str) -> int:
        return 60

    # Traza v2: el flush devuelve lo que entregó (`[{kind, wamid, ok}]`). Las
    # histories viejas traen un int: el workflow acepta las dos formas.
    @activity.defn(name="flush_pending_ui_intents_activity")
    async def fake_flush_ui_intents(session_id: str) -> list[dict]:
        tracker.flush_calls += 1
        tracker.timeline.append("flush")
        return list(flush_results or [])

    @activity.defn(name="flush_capi_outbox_activity")
    async def fake_flush_capi_outbox(session_id: str) -> dict:
        tracker.capi_flush_calls.append(session_id)
        return {"session_id": session_id, "sent": 0, "skipped": 0, "failed": 0, "pending": 0}

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        tracker.typing_calls.append(session_id)

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input: BuildPromptInput) -> list[dict]:
        tracker.build_prompt_calls.append(input)
        # `prior_history`: intercambios previos persistidos (cliente que
        # vuelve). Sin él, el historial está vacío = primer contacto.
        return [
            {"role": "system", "content": "fake-system"},
            *(prior_history or []),
            {"role": "user", "content": input.message},
        ]

    # Secuencia opcional de respuestas LLM (para escenarios multi-iteración como
    # "content + tool_call" seguido de "content final"). Si se agota o no se
    # pasa, devuelve un cierre neutro sin tools (cubre el turno ghost).
    _llm_state = {"i": 0}

    @activity.defn(name="llm_chat")
    async def fake_llm(input: LLMChatInput) -> LLMResponseData:
        tracker.llm_calls += 1
        # Hook por número de llamada (1-based): permite al test inyectar un
        # side-effect MIENTRAS el "LLM piensa" (ej. signalear el workflow con
        # un mensaje nuevo del cliente — Fase 1 interrupción). El hook corre
        # ANTES de devolver la respuesta → el signal queda en la history antes
        # de la completion de esta activity.
        hook = (llm_call_hooks or {}).get(tracker.llm_calls)
        if hook is not None:
            await hook()  # type: ignore[operator]
        if llm_responses:
            i = _llm_state["i"]
            if i < len(llm_responses):
                _llm_state["i"] += 1
                return llm_responses[i]
            return LLMResponseData(
                content="ok",
                finish_reason="stop",
                has_tool_calls=False,
                tool_calls=[],
            )
        return LLMResponseData(
            content="respuesta combinada del bot",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        )

    @activity.defn(name="execute_tool")
    async def fake_execute_tool(input: ExecuteToolInput) -> str:
        tracker.execute_tool_calls.append(input.name)
        if tool_results and input.name in tool_results:
            return tool_results[input.name]
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input: RecordTurnInput) -> None:
        tracker.record_turn_calls += 1
        tracker.record_turn_new_messages.append(list(input.new_messages))

    # Traza v2: el envío devuelve las burbujas entregadas con su wamid. Con
    # `send_returns_none` se simula la forma vieja (histories previas: None).
    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send_whatsapp(session_id: str, message: str) -> list[dict] | None:
        tracker.send_whatsapp_calls.append((session_id, message))
        tracker.timeline.append(f"send:{message}")
        if send_returns_none:
            return None
        return [{"wamid": f"wamid.out{len(tracker.send_whatsapp_calls)}", "text": message}]

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(
        session_id: str, message: str, tools_used: list[str] | None = None
    ) -> None:
        tracker.persist_calls.append((session_id, message))

    @activity.defn(name="decide_ghosting_action")
    async def fake_ghosting() -> str:
        tracker.ghosting_calls += 1
        # Hook por número de ciclo ghost (1-based): permite signalear el
        # workflow MIENTRAS corre esta activity — el mensaje queda en
        # `_pending` ANTES de que el workflow appendée el trigger (orden
        # determinista: signal < trigger). Simula al cliente escribiendo en
        # la ventana entre el timeout y el turno de cierre (premortem C2).
        hook = (ghosting_call_hooks or {}).get(tracker.ghosting_calls)
        if hook is not None:
            await hook()  # type: ignore[operator]
        return "[GHOST] auto-tagging"

    # Red de seguridad orden↔tag (premortem C3, run 5f43bcd0): el resultado
    # es inyectable para simular la rama `escalated=True` sin vault real.
    @activity.defn(name="ensure_payment_pending_closure")
    async def fake_ensure_payment_pending_closure(
        session_id: str, order_id: str, motivo: str
    ) -> PaymentPendingClosureResult:
        tracker.ensure_closure_calls.append((session_id, order_id, motivo))
        if payment_closure_result is not None:
            return payment_closure_result
        return PaymentPendingClosureResult(acted=False, escalated=False)

    # Red de seguridad patrón A (closing tags que exigen escalación).
    @activity.defn(name="ensure_closing_escalation")
    async def fake_ensure_closing_escalation(
        session_id: str, reason_category: str, motivo: str
    ) -> bool:
        tracker.closing_escalation_calls.append(
            (session_id, reason_category, motivo)
        )
        return closing_escalation_result

    # Dispatcher activities — registradas para que el worker las acepte aun
    # cuando el workflow las ignore en este test.
    @activity.defn(name="start_or_signal_sales_workflow")
    async def fake_start_sales(decision) -> None:
        tracker.start_sales_calls += 1

    @activity.defn(name="schedule_remarketing_workflow")
    async def fake_schedule_remarketing(decision) -> None:
        pass

    # HU-003 A7: run_agent_turn resuelve el episodio activo (detrás de
    # workflow.patched("cost-attribution-episode-v1")) para atribuir el costo
    # del LLM — los fresh runs lo invocan, así que el worker debe registrarlo.
    @activity.defn(name="get_active_episode_id")
    async def fake_get_active_episode_id(session_id: str) -> str:
        return "ep_001"

    @activity.defn(name="reset_llm_history_for_episode")
    async def fake_reset_llm_history(inp: ResetLLMHistoryInput) -> bool:
        tracker.history_reset_calls.append(inp.workspace_path)
        tracker.timeline.append("reset_llm_history")
        return False

    # Saludo determinista de primer contacto (runs dc32f7fe /
    # 3ce50ef3): la activity real lee la hora de Bogotá; acá
    # devolvemos la Burbuja 1 nocturna fija.
    @activity.defn(name="build_first_contact_greeting")
    async def fake_first_contact_greeting() -> str:
        tracker.first_contact_greeting_calls += 1
        return FIRST_CONTACT_GREETING

    # Guarda de enumeración de variantes (run 9bd495be): la activity real
    # detecta 4+ aromas/colores del catálogo en el texto final y encola el
    # picker; acá devolvemos lo que el escenario pida.
    @activity.defn(name="apply_variant_enumeration_guard")
    async def fake_variant_guard(session_id: str, final_text: str) -> bool:
        tracker.variant_guard_calls.append(final_text)
        return variant_guard_result

    @activity.defn(name="persist_turn_trace")
    async def fake_persist_turn_trace(session_id: str, payload_json: str) -> bool:
        tracker.turn_traces.append(json.loads(payload_json))
        return True

    # Costo del turno al episodio: solo corre cuando el LLM reporta `usage`.
    @activity.defn(name="record_episode_llm_usage")
    async def fake_record_episode_llm_usage(input: RecordEpisodeLLMUsageInput) -> None:
        return None

    return [
        fake_record_episode_llm_usage,
        fake_persist_turn_trace,
        fake_variant_guard,
        fake_first_contact_greeting,
        fake_bootstrap,
        fake_read_handoff,
        fake_read_order_draft_note,
        fake_read_idle_timeout,
        fake_flush_ui_intents,
        fake_flush_capi_outbox,
        fake_typing,
        fake_build_prompt,
        fake_llm,
        fake_execute_tool,
        fake_record_turn,
        fake_send_whatsapp,
        fake_persist,
        fake_ghosting,
        fake_ensure_payment_pending_closure,
        fake_ensure_closing_escalation,
        fake_start_sales,
        fake_schedule_remarketing,
        fake_get_active_episode_id,
        fake_reset_llm_history,
    ]


@pytest.mark.asyncio
async def test_two_signals_coalesce_into_single_turn(tmp_path: Path) -> None:
    """Regresion del bug b9639be1: dos signals consecutivos NO deben producir
    dos turnos LLM ni dos respuestas de WhatsApp."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker, workspace_path=str(workspace)
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_test1",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_test1",
                task_queue=SALES_QUEUE,
            )

            # Dos signals back-to-back, simulando "Hola si" + "Me recuerdas..."
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Hola si", None, None],
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Me recuerdas cuanto vale plegaria de luz?", None, None],
            )

            # El idle timeout (1min) seguido del shutdown ghost-detect
            # terminara el workflow. Esperar a que cierre.
            await handle.result()

    # ASSERT: con coalesce activado, los dos mensajes se procesan en UN turno.
    # Pre-fix: tracker.llm_calls == 2 (bug). Post-fix: == 2 (1 user turn + 1 ghost).
    # El ghost trigger lo emite el wait_condition timeout y tambien dispara LLM
    # (mensaje sintetico GHOST). Lo que NOS interesa: cuantos send_whatsapp
    # se mandaron PARA el cliente.
    #
    # En el path coalesce: 1 turno user (envia WhatsApp) + 1 turno ghost
    # (NO envia WhatsApp porque _force_shutdown=True bloquea el send_whatsapp).
    assert len(tracker.send_whatsapp_calls) == 1, (
        f"Coalesce roto: deberia haber 1 sola respuesta del bot, "
        f"hubo {len(tracker.send_whatsapp_calls)}: {tracker.send_whatsapp_calls}"
    )

    # build_prompt recibio AMBOS mensajes concatenados en una sola llamada
    # (mas la llamada del ghost trigger turn).
    user_turn_calls = [
        c for c in tracker.build_prompt_calls if c.message != "[GHOST] auto-tagging"
    ]
    assert len(user_turn_calls) == 1, (
        f"Esperaba 1 sola llamada user a build_prompt, hubo {len(user_turn_calls)}"
    )
    combined = user_turn_calls[0].message
    assert "Hola si" in combined
    assert "Me recuerdas cuanto vale plegaria de luz?" in combined


@pytest.mark.asyncio
async def test_handoff_from_metadata_goes_to_plugin_context(tmp_path: Path) -> None:
    """Fix 3: el handoff escrito por el dispatcher en metadata se consume
    al arrancar Sales y se entrega como `plugin_context` (NO como user msg)
    cuando hay un mensaje real del cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handoff_summary = "Cliente respondio: 'Hola si' al gancho de remarketing"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                pending_handoff=handoff_summary,
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_test2",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_test2",
                task_queue=SALES_QUEUE,
            )

            # Cliente manda su mensaje real poco despues del handoff
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Me recuerdas el precio?", None, None],
            )

            await handle.result()

    # Buscamos la llamada user (no la del ghost)
    user_turn_calls = [
        c for c in tracker.build_prompt_calls if "GHOST" not in c.message
    ]
    assert len(user_turn_calls) == 1
    bp = user_turn_calls[0]
    # 1. El message del rol "user" es solo el mensaje del cliente, NO el handoff
    assert bp.message == "Me recuerdas el precio?"
    # 2. El handoff vive en plugin_context con el marker explicito
    assert bp.plugin_context is not None
    assert any(
        "[HANDOFF_REMARKETING]" in ctx and handoff_summary in ctx
        for ctx in bp.plugin_context
    )


@pytest.mark.asyncio
async def test_handoff_turn_carries_order_draft_note(tmp_path: Path) -> None:
    """Incidente 2026-07-17 (run 019f6db3): el turno de handoff arrancaba SIN
    el bloque `[DATOS DEL PEDIDO YA CONFIRMADOS]` aunque el draft estuviera
    intacto en el vault — el LLM re-preguntó (y pisó) lo ya elegido. El
    workflow debe adjuntar la note del draft al plugin_context del handoff."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handoff_summary = "Usuario respondió: Vamos con esas dos"
    draft_note = (
        "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE, metadata]\n"
        "Notas: 1× Leo café + 1× Libra sándalo"
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                pending_handoff=handoff_summary,
                order_draft_note=draft_note,
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_draftnote",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_draftnote",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Vamos con esas dos", None, None],
            )
            await handle.result()

    user_turn_calls = [
        c for c in tracker.build_prompt_calls if "GHOST" not in c.message
    ]
    assert len(user_turn_calls) == 1
    bp = user_turn_calls[0]
    assert bp.plugin_context is not None
    assert any("DATOS DEL PEDIDO YA CONFIRMADOS" in ctx for ctx in bp.plugin_context), (
        f"draft note ausente del plugin_context: {bp.plugin_context}"
    )


@pytest.mark.asyncio
async def test_typing_indicator_fires_before_llm(tmp_path: Path) -> None:
    """Fix 5: el typing indicator se dispara antes del LLM en cada turno."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker, workspace_path=str(workspace)
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_test3",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_test3",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["hola", None, None],
            )
            await handle.result()

    # Hubo al menos 1 typing indicator (el del turno user). El ghost turn
    # tambien puede dispararlo, pero como minimo el user turn debe.
    assert len(tracker.typing_calls) >= 1
    assert tracker.typing_calls[0] == "wa_test3"


@pytest.mark.asyncio
async def test_pre_tool_content_is_never_sent(tmp_path: Path) -> None:
    """Default-deny del content junto a tool calls (run 1c9ef231, 2026-08-19).

    En ese run el LLM emitió "Encontré 10 velas religiosas. Las muestro al
    cliente." JUNTO a `present_products` — y la whitelist PRESENTATIONAL_TOOLS
    lo forwardeó como burbuja al cliente (la narración salió a las 17:22:00,
    el menú a las 17:22:04). La whitelist clasificaba el texto POR EL BATCH de
    tools, pero el batch no determina la naturaleza del texto.

    Contrato nuevo (erradica la clase): el texto que acompaña una tool call es
    narración interna SIEMPRE — se descarta y se loguea. El texto para el
    cliente viaja en los params de la tool (`intro_text`, `body`), que toda
    tool presentacional ya exige. Ni el caso "saludo + send_quick_replies"
    (run ddd0d472) forwardea: el saludo va en el `body` de la tool.

    Contrato L-11 (se preserva): `send_quick_replies` sigue siendo TURN-ENDING.
    """
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # El texto LITERAL del run ("Encontré 10 velas religiosas. Las muestro al
    # cliente.") ya lo caza el tripwire `looks_like_admin_leak` (ver
    # tests/platform/test_admin_leak_detector.py). Acá usamos una narración
    # que el detector NO huele, para exigir el default-deny ESTRUCTURAL: sin
    # él, esta burbuja saldría aunque todos los detectores estén verdes.
    narration = "Perfecto, encontré 10 opciones en el catálogo. Armo el menú."
    responses = [
        # Turno user: narración de proceso + present_products (el run real).
        LLMResponseData(
            content=narration,
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_1",
                    name="present_products",
                    arguments={
                        "handles": ["vela-1"],
                        "intro_text": "Estas son nuestras velas religiosas:",
                    },
                )
            ],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker, workspace_path=str(workspace), llm_responses=responses
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_catalogo",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_catalogo",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["hola quiero ver los productos religiosos", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    # La narración NO llega al cliente como burbuja.
    assert narration not in sent, (
        f"La narración de proceso llegó al cliente: {sent}"
    )
    # Tampoco se persiste al dashboard como mensaje del agente.
    persisted = [m for (_sid, m) in tracker.persist_calls]
    assert narration not in persisted, (
        f"Narración interna persistida como mensaje del agente: {persisted}"
    )
    # El flush de UI intents sí corrió — el menú (canal legítimo, via
    # `intro_text`) sigue saliendo.
    assert tracker.flush_calls >= 1
    # L-11 se preserva: present_products corta el turno (1 user + 1 ghost).
    assert tracker.llm_calls == 2, (
        f"El turno debió cortar tras present_products "
        f"(1 user + 1 ghost). llm_calls={tracker.llm_calls}"
    )


@pytest.mark.asyncio
async def test_greeting_next_to_quick_replies_travels_in_body_param(
    tmp_path: Path,
) -> None:
    """El caso que motivó el forward viejo (saludo + send_quick_replies, run
    ddd0d472) tampoco forwardea: el canal del saludo es el param `body` de la
    tool — el content suelto se descarta como narración (default-deny)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    greeting = "Buenos dias. Bienvenido a Hubara, velas artesanales hechas a mano."
    responses = [
        LLMResponseData(
            content=greeting,
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_1",
                    name="send_quick_replies",
                    arguments={"body": greeting, "buttons": []},
                )
            ],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker, workspace_path=str(workspace), llm_responses=responses
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_greet",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_greet",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Hola", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert greeting not in sent, (
        f"El content junto a send_quick_replies se forwardeó (el saludo "
        f"viaja en el param `body`, no como burbuja suelta): {sent}"
    )
    persisted = [m for (_sid, m) in tracker.persist_calls]
    assert greeting not in persisted
    # Los botones (canal legítimo) sí se flushean.
    assert tracker.flush_calls >= 1
    # L-11 se preserva: send_quick_replies corta el turno (1 user + 1 ghost).
    assert tracker.llm_calls == 2, (
        f"El turno debió cortar tras send_quick_replies "
        f"(1 user + 1 ghost). llm_calls={tracker.llm_calls}"
    )


@pytest.mark.asyncio
async def test_self_transfer_decision_is_noop_and_sends_nothing(
    tmp_path: Path,
) -> None:
    """L-12 (run 3607aecc): autotransferencia dentro de sales = noop total.

    El LLM de ventas, al recibir el handoff de remarketing, llamó
    `transfer_to_sales_agent` (transferirse a sí mismo). El workflow legacy
    ejecutaba el self-loop `start_or_signal_sales_workflow` — que PISABA el
    `pending_handoff_summary` ajeno (perdió el "Dame 3" del cliente) — y el
    LLM regurgitaba el `message` interno de la tool como respuesta final:
    "El control ha sido transferido al agente de ventas." llegó al cliente.

    Contrato L-12: transfer_decision dentro de sales → ni activity ni burbuja.
    """
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    transfer_payload = json.dumps(
        {
            "transfer_decision": {
                "session_id": "wa_selftx",
                "target_route": "ventas",
                "summary": "Cliente retomó remarketing",
            },
            "message": (
                "El control ha sido transferido. NO generes más texto, "
                "responde vacío o con 'Ok' para finalizar."
            ),
        },
        ensure_ascii=False,
    )
    responses = [
        # Iter 1: el LLM "se transfiere" (tool interna, sin content).
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_tx",
                    name="transfer_to_sales_agent",
                    arguments={"resumen": "Cliente retomó remarketing"},
                )
            ],
        ),
        # Iter 2: regurgita la jerga interna del tool result (el bug real).
        LLMResponseData(
            content="El control ha sido transferido al agente de ventas.",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                pending_handoff="Cliente respondió 'A sí' al recordatorio",
                llm_responses=responses,
                tool_results={"transfer_to_sales_agent": transfer_payload},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_selftx",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_selftx",
                task_queue=SALES_QUEUE,
            )
            await handle.result()

    # 1. El self-loop NO corre: no se pisa el handoff de nadie.
    assert tracker.start_sales_calls == 0, (
        "start_or_signal_sales_workflow corrió en una autotransferencia — "
        "pisa pending_handoff ajeno (L-12)."
    )
    # 2. La jerga interna NO viaja al cliente (ni nada de ese turno).
    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert sent == [], (
        f"La autotransferencia no debe producir burbujas. Enviado: {sent}"
    )


@pytest.mark.asyncio
async def test_idle_timeout_with_pending_handoff_processes_it_not_ghosting(
    tmp_path: Path,
) -> None:
    """L-12 Fix D (run 3607aecc): handoff dormido se procesa, no se ghostea.

    El handoff viaja por metadata (no despierta el wait_condition). Un mensaje
    del cliente convertido en handoff durante la ventana de transferencia
    ("Usuario respondió: Dame 3") quedaba dormido hasta el idle timeout — y el
    flujo viejo lo coalesceaba JUNTO al trigger de ghosting con
    `_force_shutdown=True`, suprimiendo la respuesta: el cliente nunca recibía
    nada y el ciclo re-abría remarketing (loop).

    Contrato: al timeout, PRIMERO se lee el handoff; si hay → turno normal
    (la respuesta SE ENVÍA, sin ghosting ese ciclo). El ghosting recién corre
    en el ciclo siguiente, si de verdad no hay nada.
    """
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                # Call 1 (bootstrap): nada. Call 2 (chequeo del timeout): el
                # handoff escrito mientras el workflow dormía. Resto: None.
                handoff_sequence=[None, "Usuario respondió: Dame 3"],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_lateh",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_lateh",
                task_queue=SALES_QUEUE,
            )
            # Sin signals: el cliente "ya habló" pero su mensaje quedó en el
            # handoff de metadata. Solo el idle timeout despierta al workflow.
            await handle.result()

    # 1. La respuesta al handoff SE ENVIÓ (el flujo viejo la suprimía).
    #    (El sanitizador anti-prefijo puede strippear "respuesta c..." del
    #    string del fake — se asertea por sufijo, no por igualdad.)
    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert len(sent) == 1 and "combinada del bot" in sent[0], (
        f"La respuesta al handoff dormido no llegó al cliente. Enviado: {sent}"
    )
    # 2. El turno usó el framing L-12 (no el summary crudo) como user message.
    handoff_turns = [
        c for c in tracker.build_prompt_calls if "Dame 3" in c.message
    ]
    assert len(handoff_turns) == 1, (
        f"Esperaba 1 turno del handoff, hubo {len(handoff_turns)}: "
        f"{[c.message for c in tracker.build_prompt_calls]}"
    )
    assert "HANDOFF DE REMARKETING A VENTAS" in handoff_turns[0].message
    # 3. El ghosting corrió EXACTAMENTE una vez — en el ciclo siguiente
    #    (handoff ya drenado), no en el ciclo del handoff.
    assert tracker.ghosting_calls == 1, (
        f"Ghosting debió correr solo en el 2º ciclo idle. "
        f"ghosting_calls={tracker.ghosting_calls}"
    )


@pytest.mark.asyncio
async def test_burst_injects_thread_awareness_note(tmp_path: Path) -> None:
    """Ráfaga de 3 mensajes → el turno lleva una nota de ráfaga determinista en
    `plugin_context` para que el LLM responda al hilo COMPLETO (el patrón "el
    bot solo ve uno"). Los 3 se coalescen en un turno (como el debounce) pero
    además el LLM recibe la lista explícita de lo que el cliente escribió."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(tracker, workspace_path=str(workspace)),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_burst",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_burst",
                task_queue=SALES_QUEUE,
            )
            for text in ("Hola", "quiero el difusor", "de lavanda"):
                await handle.signal(
                    HubaraSalesSessionWorkflow.send_message,
                    args=[text, None, None],
                )
            await handle.result()

    user_calls = [c for c in tracker.build_prompt_calls if "GHOST" not in c.message]
    assert len(user_calls) == 1, (
        f"Esperaba 1 turno user coalescido, hubo {len(user_calls)}"
    )
    bp = user_calls[0]
    # Los 3 mensajes concatenados en el rol user (coalesce por seq).
    assert "Hola" in bp.message
    assert "quiero el difusor" in bp.message
    assert "de lavanda" in bp.message
    # Nota de ráfaga determinista en plugin_context (conciencia de hilo).
    assert bp.plugin_context is not None, (
        "Falta la nota de ráfaga: el LLM no sabe que fueron 3 mensajes seguidos"
    )
    assert any("3 mensajes seguidos" in c for c in bp.plugin_context), (
        f"La nota de ráfaga no lista los mensajes. plugin_context={bp.plugin_context}"
    )


@pytest.mark.asyncio
async def test_burst_dedupes_repeated_plugin_context(tmp_path: Path) -> None:
    """Ráfaga donde cada inbound trae el MISMO plugin_context (el caso real:
    `LoadOrStartSalesSession` inyecta el bloque bogota-context en cada signal)
    → el turno lleva UNA sola copia, no N (burst-note-v2). Con v1, una ráfaga
    de 3 metía 3 bloques "Hora actual en Colombia" al system prompt."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bogota = "[CONTEXTO DE TURNO] Hora actual en Colombia: 14:30"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(tracker, workspace_path=str(workspace)),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_burstctx",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_burstctx",
                task_queue=SALES_QUEUE,
            )
            for text in ("Hola", "quiero el difusor", "de lavanda"):
                await handle.signal(
                    HubaraSalesSessionWorkflow.send_message,
                    args=[text, None, [bogota]],
                )
            await handle.result()

    user_calls = [c for c in tracker.build_prompt_calls if "GHOST" not in c.message]
    assert len(user_calls) == 1
    bp = user_calls[0]
    assert bp.plugin_context is not None
    copies = bp.plugin_context.count(bogota)
    assert copies == 1, (
        f"El bloque bogota-context debe deduplicarse en ráfaga (v2): "
        f"esperaba 1 copia, hay {copies}. plugin_context={bp.plugin_context}"
    )
    # La conciencia de ráfaga se preserva junto con el dedupe.
    assert any("3 mensajes seguidos" in c for c in bp.plugin_context)


def _tool_resp(*names: str) -> LLMResponseData:
    return LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(id=f"t{i}", name=n, arguments={})
            for i, n in enumerate(names, 1)
        ],
    )


def _final_resp(text: str) -> LLMResponseData:
    return LLMResponseData(
        content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[]
    )


@pytest.mark.asyncio
async def test_present_products_turn_sends_single_bubble(tmp_path: Path) -> None:
    """Redundancia catálogo (run eda8d460): tras `present_products` el turno
    CORTA — el intro_text del catálogo ES el mensaje. Sin el corte, el LLM
    emitía otro texto ("Aquí tienes todas nuestras velas...") repitiendo el
    intro → el cliente veía dos burbujas diciendo lo mismo."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    _tool_resp("search_products"),
                    _tool_resp("present_products"),
                    _final_resp("NO DEBE SALIR: texto redundante post-catálogo"),
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_catalogo",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_catalogo",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["[el cliente tocó el botón: Ver catálogo]", None, None],
            )
            await handle.result()

    assert "present_products" in tracker.execute_tool_calls
    redundant = [m for (_s, m) in tracker.send_whatsapp_calls if "NO DEBE SALIR" in m]
    assert not redundant, (
        "El turno debió cortar en present_products (el catálogo ya es el "
        f"mensaje); salió una burbuja redundante: {tracker.send_whatsapp_calls}"
    )


@pytest.mark.asyncio
async def test_new_message_mid_llm_restarts_turn(tmp_path: Path) -> None:
    """Fase 1 "corrientazo" (run eda8d460, caso contra-entrega): si el cliente
    escribe MIENTRAS el LLM piensa y el turno aún no tocó al cliente, el turno
    se aborta limpio y se recompone con TODO (viejo + nuevo). El cliente recibe
    UNA respuesta que considera ambos mensajes — no dos respuestas cruzadas."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handle_box: dict = {}

    async def _signal_mid_llm() -> None:
        await handle_box["handle"].signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["Quiero el pago contra entrega", None, None],
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    _final_resp("Respuesta uno: solo considera el primer mensaje"),
                    _final_resp("Respuesta final: considera ambos"),
                ],
                llm_call_hooks={1: _signal_mid_llm},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_corrientazo",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_corrientazo",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["[datos de envío recibidos] pago=transferencia", None, None],
            )
            await handle.result()

    # El turno abortado NO llegó al cliente.
    texts = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert all("Respuesta uno" not in t for t in texts), (
        f"La respuesta stale del turno abortado llegó al cliente: {texts}"
    )
    assert any("Respuesta final" in t for t in texts), (
        f"La respuesta recompuesta no llegó: {texts}"
    )
    # El turno recompuesto vio AMBOS mensajes.
    user_calls = [c for c in tracker.build_prompt_calls if "GHOST" not in c.message]
    assert len(user_calls) == 2, (
        f"Esperaba 2 build_prompt de usuario (abortado + recompuesto), "
        f"hubo {len(user_calls)}"
    )
    recomposed = user_calls[-1]
    assert "datos de envío recibidos" in recomposed.message
    assert "contra entrega" in recomposed.message


@pytest.mark.asyncio
async def test_stale_final_text_suppressed_after_outbound(tmp_path: Path) -> None:
    """Fase 1, mitad B (run eda8d460, caso "Solo plegaria de luz"): si el turno
    YA tocó al cliente (encoló cards) no hay restart limpio — pero el TEXTO de
    cierre stale se suprime cuando el cliente escribió en el interín. El
    mensaje nuevo se procesa en el turno siguiente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handle_box: dict = {}

    async def _signal_mid_llm() -> None:
        await handle_box["handle"].signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["Solo plegaria de luz", None, None],
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    _tool_resp("present_product_detail"),
                    _final_resp("NO DEBE SALIR: ¿cuál de las dos te gusta más?"),
                    _final_resp("Perfecto, solo la Plegaria de Luz entonces"),
                ],
                # El signal llega mientras el LLM compone el cierre (llamada 2).
                llm_call_hooks={2: _signal_mid_llm},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_stale",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_stale",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["?", None, None],
            )
            await handle.result()

    texts = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert all("NO DEBE SALIR" not in t for t in texts), (
        f"El cierre stale salió igual después de que el cliente escribió: {texts}"
    )
    assert any("Perfecto, solo la Plegaria" in t for t in texts), (
        f"El turno del mensaje nuevo no respondió: {texts}"
    )
    # Las cards del turno interrumpido SÍ salieron (ya habían tocado al cliente).
    assert "present_product_detail" in tracker.execute_tool_calls


@pytest.mark.asyncio
async def test_record_turn_persists_the_user_message(tmp_path: Path) -> None:
    """Off-by-one contra upstream (caso 573229041190, 2026-07-07): exoclaw
    `loop.py` graba `all_msgs[len(initial) - 1:]` — el -1 INCLUYE el mensaje
    del usuario. El adapter usaba `messages[initial_len:]` (sin -1) y desde
    abril NINGÚN mensaje del cliente entraba al historial durable del LLM:
    el bot solo veía sus propios mensajes + tool results (verificado en el
    history real: 82 mensajes, user:1). Consecuencia: "Ya te los di" — el
    cliente repitiendo datos que el bot no podía recordar."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker, workspace_path=str(workspace)
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_recorduser",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_recorduser",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Arborizadora, calle 59b sur 38", None, None],
            )
            await handle.result()

    # El turno del usuario (no el ghost) debe persistir PRIMERO el user msg.
    user_turns = [
        msgs
        for msgs in tracker.record_turn_new_messages
        if any(
            m.get("role") == "user"
            and "Arborizadora" in str(m.get("content", ""))
            for m in msgs
        )
    ]
    assert user_turns, (
        "record_turn nunca recibió el mensaje del cliente — el historial del "
        f"LLM queda sin turnos user. Capturado: {tracker.record_turn_new_messages}"
    )
    first = user_turns[0][0]
    assert first.get("role") == "user", (
        f"El user msg debe ir PRIMERO en new_messages (orden del turno), "
        f"vino: {user_turns[0]}"
    )


@pytest.mark.asyncio
async def test_ghost_shutdown_cancelled_when_customer_interrupts(
    tmp_path: Path,
) -> None:
    """Run 48ec6df5 (caso 573229041190, 2026-07-17): el cliente clickeó
    "Ver catálogo" MIENTRAS corría el turno de auto-etiquetado del ghosting.
    El corrientazo recompuso el batch (consumiendo el mensaje de `_pending`)
    pero `_force_shutdown` quedó prendido: el turno recompuesto ejecutó
    `present_products`, el flush de UI intents se salteó (el MPM del catálogo
    nunca salió a WhatsApp) y la sesión se apagó "por abandono" con el cliente
    activo. El cliente tuvo que volver a escribir 10 min después.

    Post-fix: el corrientazo invalida la premisa del ghosting → se limpia
    `_force_shutdown`, el flush corre tras el turno recompuesto y la sesión
    sigue viva (un SEGUNDO ciclo de ghosting la cierra recién cuando el
    cliente de verdad abandona)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handle_box: dict = {}

    async def _click_mid_ghost_turn() -> None:
        await handle_box["handle"].signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["[el cliente tocó el botón: Ver catálogo]", None, None],
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    # Turno 1: saludo (el turno queda esperando respuesta).
                    _final_resp("¡Hola! ¿Querés ver el catálogo?"),
                    # Turno ghost (llamada 2, hookeada): el click llega
                    # mientras este LLM "piensa" → corrientazo, se descarta.
                    _final_resp("[cierre ghost stale — NO DEBE SALIR]"),
                    # Turno recompuesto: presenta el catálogo (turno corta).
                    _tool_resp("present_products"),
                    # Llamada 4+: segundo ciclo ghost → default "ok".
                ],
                llm_call_hooks={2: _click_mid_ghost_turn},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_ghostrace",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_ghostrace",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Hola", None, None],
            )
            await handle.result()

    # El click del cliente se procesó (sanity — esto pasaba incluso pre-fix).
    assert "present_products" in tracker.execute_tool_calls, (
        f"El turno recompuesto no ejecutó present_products: "
        f"{tracker.execute_tool_calls}"
    )
    # El catálogo SALIÓ: el flush de UI intents corrió tras el turno
    # recompuesto (flush #1 = turno del saludo, flush #2 = turno del click).
    # Pre-fix: _force_shutdown seguía prendido → flush salteado → 1.
    assert tracker.flush_calls == 2, (
        f"El flush de UI intents no corrió tras el turno recompuesto — el "
        f"catálogo quedó encolado sin enviar. flush_calls={tracker.flush_calls}"
    )
    # La sesión sobrevivió al primer ghosting (el cliente volvió): la cierra
    # recién un SEGUNDO ciclo de ghosting. Pre-fix: se apagaba en el primero.
    assert tracker.ghosting_calls == 2, (
        f"La sesión debió sobrevivir al primer ghosting (cliente activo) y "
        f"cerrarse en el segundo. ghosting_calls={tracker.ghosting_calls}"
    )


@pytest.mark.asyncio
async def test_handoff_turn_no_message_sentinel_suppresses_send(
    tmp_path: Path,
) -> None:
    """Incidente wa_573125671604 (2026-07-17, 23:15 UTC): remarketing
    transfirió a Sales sin mensaje nuevo del cliente y con la venta ya
    cerrada (pedido registrado). El LLM de Sales declinó en prosa ("No hay
    mensaje nuevo del cliente... No genero respuesta") y esa deliberación
    se envió al cliente por WhatsApp y quedó en el historial.

    Contrato nuevo (gated `no-message-abstention-v1`, mismo canal que
    remarketing): si el LLM responde el sentinel NO_MESSAGE, el turno NO
    envía y NO persiste — Sales sigue siendo dueño de la conversación y el
    workflow continúa normal (ghost cierra después como siempre)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                pending_handoff="Usuario respondió: (sin mensaje nuevo)",
                llm_responses=[
                    LLMResponseData(
                        content="NO_MESSAGE",
                        finish_reason="stop",
                        has_tool_calls=False,
                        tool_calls=[],
                    ),
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_abstention_sales",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_abstention_sales",
                task_queue=SALES_QUEUE,
            )
            await handle.result()

    assert tracker.llm_calls >= 1, "el turno de handoff debe correr"
    assert tracker.send_whatsapp_calls == [], (
        "abstención → NINGÚN mensaje al cliente"
    )
    assert tracker.persist_calls == [], (
        "abstención → nada en el historial del dashboard"
    )


# Resumen administrativo real del incidente (run 5f43bcd0, evento 783) — el
# texto que el cliente NUNCA debió ver.
_ADMIN_SUMMARY = (
    "La conversación queda etiquetada como `INTERESADO`. El cliente eligió "
    "su *Duo Zodiacal Leo* en *Amarillo* y estaba por escoger el aroma "
    "cuando se retiró. Remarketing automático activado. 🤍"
)


@pytest.mark.asyncio
async def test_admin_close_summary_never_reaches_customer_after_corrientazo(
    tmp_path: Path,
) -> None:
    """Run 5f43bcd0 (caso 573229041190, 2026-08-13, evento 783): el cliente
    escribió "Caballero" MIENTRAS corría el turno de auto-etiquetado del
    ghosting. El corrientazo recompuso el batch y (fix run 48ec6df5) limpió
    `_force_shutdown` — pero el batch recompuesto CONSERVÓ el trigger
    [SISTEMA] de ghosting. El LLM, con ese prompt híbrido, etiquetó
    INTERESADO y emitió el resumen administrativo como texto final. Con
    `_force_shutdown` ya apagado, el guard del send lo dejó pasar: el
    cliente recibió "La conversación queda etiquetada como `INTERESADO`...".

    Contrato nuevo: el texto de un turno administrativo NUNCA llega al
    canal del cliente, sin importar en qué quedó `_force_shutdown`. La
    supresión es estructural (el batch contiene el trigger de ghosting →
    turno admin → no se envía ni persiste), no una confluencia de flags."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handle_box: dict = {}

    async def _customer_writes_mid_ghost_turn() -> None:
        await handle_box["handle"].signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["Caballero", None, None],
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    # Turno 1: saludo normal.
                    _final_resp("¡Hola! ¿Qué aroma te gustaría?"),
                    # Turno ghost (llamada 2, hookeada): "Caballero" llega
                    # mientras este LLM piensa → corrientazo, se descarta.
                    _final_resp("[cierre ghost stale — NO DEBE SALIR]"),
                    # Turno recompuesto: el LLM replica el incidente —
                    # etiqueta y después "reporta" el cierre en prosa.
                    _tool_resp("manage_conversation_tag"),
                    _final_resp(_ADMIN_SUMMARY),
                    # Llamadas 5+: segundo ciclo ghost → default "ok".
                ],
                llm_call_hooks={2: _customer_writes_mid_ghost_turn},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_adminleak",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_adminleak",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Hola", None, None],
            )
            await handle.result()

    # Sanity del wiring: el saludo del turno 1 SÍ salió.
    assert any(
        "aroma" in m for (_s, m) in tracker.send_whatsapp_calls
    ), f"el turno normal debió enviar el saludo: {tracker.send_whatsapp_calls}"
    # EL BUG: el resumen administrativo del cierre jamás va al cliente.
    leaked = [
        m
        for (_s, m) in tracker.send_whatsapp_calls
        if "etiquetada" in m.lower() or "remarketing" in m.lower()
    ]
    assert not leaked, (
        f"texto administrativo enviado al cliente por WhatsApp: {leaked}"
    )
    # Tampoco al historial del dashboard.
    persisted = [
        m
        for (_s, m) in tracker.persist_calls
        if "etiquetada" in m.lower() or "remarketing" in m.lower()
    ]
    assert not persisted, (
        f"texto administrativo persistido al historial: {persisted}"
    )
    # Fix 1 (drop del trigger): el corrientazo invalida la premisa del
    # ghosting → el turno recompuesto corre sobre lo que el cliente escribió,
    # SIN el trigger [SISTEMA] coalesceado (el prompt híbrido fue la raíz
    # del incidente).
    recomposed = [
        b.message
        for b in tracker.build_prompt_calls
        if "Caballero" in b.message
    ]
    assert recomposed, (
        "el turno recompuesto debió correr sobre el mensaje real del cliente"
    )
    assert all("[GHOST]" not in m for m in recomposed), (
        f"el trigger de ghosting no debe viajar en el prompt recompuesto: "
        f"{recomposed}"
    )


@pytest.mark.asyncio
async def test_message_during_ghost_window_gets_answered_not_swallowed(
    tmp_path: Path,
) -> None:
    """Premortem C2 (simétrico inverso del run 5f43bcd0): el cliente escribe
    justo cuando el timer de ghosting acaba de disparar — el mensaje cae en
    la ventana entre la inyección del trigger y el turno de cierre, y se
    coalescea CON el trigger bajo `_force_shutdown=True`. Resultado viejo:
    la respuesta del turno se suprime, el cancel-shutdown del cierre no ve
    nada pendiente, y la sesión se apaga con el mensaje del cliente tragado.

    Contrato nuevo: un mensaje real del cliente en el batch invalida la
    premisa del ghosting ANTES del turno — el trigger se dropea, el flag se
    limpia, y el turno responde normal. La sesión la cierra recién un
    SEGUNDO ciclo de ghosting."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    handle_box: dict = {}

    async def _writes_as_ghost_timer_fires() -> None:
        await handle_box["handle"].signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["Perdona, me llamaron. ¿Seguimos?", None, None],
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[
                    # Turno 1: saludo normal.
                    _final_resp("¡Hola! ¿Qué aroma te gustaría?"),
                    # Turno post-invalidación: respuesta normal al cliente.
                    _final_resp("¡Acá sigo! Tenemos lavanda y vainilla."),
                    # Llamadas 3+: segundo ciclo ghost → default "ok".
                ],
                ghosting_call_hooks={1: _writes_as_ghost_timer_fires},
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_ghostwindow",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_ghostwindow",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Hola", None, None],
            )
            await handle.result()

    # El mensaje del cliente recibió respuesta REAL (no fue tragado).
    assert any(
        "Acá sigo" in m for (_s, m) in tracker.send_whatsapp_calls
    ), (
        f"el mensaje que cayó en la ventana ghost quedó sin respuesta: "
        f"{tracker.send_whatsapp_calls}"
    )
    # El turno corrió sobre el mensaje real, sin el trigger coalesceado.
    answered = [
        b.message
        for b in tracker.build_prompt_calls
        if "Seguimos" in b.message
    ]
    assert answered and all("[GHOST]" not in m for m in answered), (
        f"el trigger no debe viajar en el prompt del turno: {answered}"
    )
    # La sesión sobrevivió al primer ciclo (cliente activo) y cerró en el 2º.
    assert tracker.ghosting_calls == 2, (
        f"la sesión debió cerrar recién en el segundo ciclo de ghosting: "
        f"ghosting_calls={tracker.ghosting_calls}"
    )


@pytest.mark.asyncio
async def test_safety_net_escalation_sends_final_content_before_shutdown(
    tmp_path: Path,
) -> None:
    """Premortem C3 (run 5f43bcd0): la RED DE SEGURIDAD orden↔tag escala
    (`closure.escalated=True`) cuando el LLM registró la orden pero no emitió
    las tools de cierre. El bug: la red seteaba `_force_shutdown=True` ANTES
    de los bloques de send → el final_content legítimo del turno ("tu pedido
    quedó registrado...") se suprimía y el cliente, tras dar todos sus datos,
    recibía silencio. El path de escalación del LLM en cambio setea el flag
    DESPUÉS de los sends y sí manda la despedida.

    Contrato (gated `safety-net-shutdown-after-send-v1`): la escalación de la
    red difiere el shutdown hasta después del send/persist/flush — el cliente
    recibe la despedida Y el workflow igual escala/termina sin ghosting."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    order_motivo = (
        "Cliente confirmó pedido order_123 por $85000 COP, método transfer; "
        "falta verificación humana del pago."
    )
    register_payload = json.dumps(
        {
            "registered": True,
            "order_id": "order_123",
            "provider": "medusa",
            "order_registered": {
                "session_id": "wa_pmnet",
                "order_id": "order_123",
                "payment_method": "transfer",
                "total_cop": 85000,
                "currency": "COP",
                "motivo": order_motivo,
            },
            "summary": "Pedido registrado en Medusa con ID order_123.",
        },
        ensure_ascii=False,
    )
    responses = [
        # Iter 1: el LLM registra la orden (tool interna, sin content).
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_reg",
                    name="register_order",
                    arguments={"confirmado": True},
                )
            ],
        ),
        # Iter 2: la despedida legítima — pero SIN las tools de cierre
        # (ni manage_conversation_tag ni escalate_to_human): el caso que
        # dispara la red de seguridad.
        LLMResponseData(
            content="¡Listo! Tu pedido quedó registrado. Gracias por tu compra.",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                tool_results={"register_order": register_payload},
                payment_closure_result=PaymentPendingClosureResult(
                    acted=False, escalated=True
                ),
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_pmnet",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_pmnet",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Sí, confirmo. Mis datos: Calle 12 #3-45, Bogotá", None, None],
            )
            await handle.result()

    # 1. Sanity: la red de seguridad corrió con la decisión de la orden.
    assert tracker.ensure_closure_calls == [
        ("wa_pmnet", "order_123", order_motivo)
    ], (
        f"La red de seguridad no corrió como esperado: "
        f"{tracker.ensure_closure_calls}"
    )
    # 2. EL BUG: la despedida del turno DEBE llegar al cliente aunque la red
    #    haya escalado. (Sufijo, no igualdad — el sanitizador anti-prefijo
    #    puede strippear el inicio del string.)
    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert any("pedido quedó registrado" in m for m in sent), (
        f"La escalación de la red de seguridad suprimió el final_content — "
        f"el cliente dio sus datos y recibió silencio. Enviado: {sent}"
    )
    # 3. La despedida también se persistió al historial del dashboard.
    assert any("pedido quedó registrado" in m for (_sid, m) in tracker.persist_calls), (
        f"El final_content no se persistió: {tracker.persist_calls}"
    )
    # 4. El workflow IGUAL escaló y terminó: sin ciclo de ghosting (el
    #    shutdown por escalación cierra el workflow antes del idle timeout).
    assert tracker.ghosting_calls == 0, (
        f"El shutdown por escalación de la red no cerró el workflow — corrió "
        f"ghosting ({tracker.ghosting_calls} veces)."
    )


@pytest.mark.asyncio
async def test_closing_escalation_safety_net_sends_final_content_before_shutdown(
    tmp_path: Path,
) -> None:
    """Espejo del premortem C3 en la red patrón A: el LLM cierra el episodio
    con un tag que EXIGE escalación (CONFIRMADO_SIN_DATOS) pero NO llama
    `escalate_to_human` — `ensure_closing_escalation_activity` escala por él.
    El bug era el mismo: `_force_shutdown=True` antes de los sends suprimía
    la despedida legítima del turno. Contrato: mismo shutdown diferido
    (`safety-net-shutdown-after-send-v1`) — la despedida sale, el workflow
    igual escala/termina."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    tag_payload = json.dumps(
        {
            "ok": True,
            "episode_closed": {
                "session_id": "wa_patrona",
                "episode_id": "ep_007",
                "closing_tag": "CONFIRMADO_SIN_DATOS",
            },
            "message": "Tag CONFIRMADO_SIN_DATOS aplicada; episodio cerrado.",
        },
        ensure_ascii=False,
    )
    responses = [
        # Iter 1: el LLM cierra con el tag (tool interna, sin content) — y NO
        # llama escalate_to_human: el caso que dispara la red patrón A.
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_tag",
                    name="manage_conversation_tag",
                    arguments={"tag": "CONFIRMADO_SIN_DATOS", "motivo": "cierre"},
                )
            ],
        ),
        # Iter 2: la despedida legítima del turno.
        LLMResponseData(
            content="Quedó confirmado tu pedido, un asesor te escribe pronto.",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]

    # El cierre de episodio también dispara el EpisodeClosedEvent (watchdog)
    # + CAPI — fakes locales con el mismo shape que usa
    # tests/plugins/chats/test_sales_capi_trigger.py (no viven en el harness
    # compartido: ese archivo registra los suyos y colisionarían por nombre).
    @activity.defn(name="send_capi_event_activity")
    async def fake_send_capi(
        session_id: str, episode_id: str, event_name: str
    ) -> dict:
        # Shape de CapiEventResult (dict → dataclass via payload converter).
        return {
            "status": "sent",
            "event_id": f"close_{episode_id}",
            "event_name": event_name,
        }

    @activity.defn(name="orchestration.dispatch_event")
    async def fake_dispatch_event(envelope) -> dict:
        # Shape mínimo de DispatchResult.
        return {
            "source_plugin": "chats",
            "source_worker": "sales",
            "event_type": "chats.episode_closed",
        }

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=[
                *_make_fake_activities(
                    tracker,
                    workspace_path=str(workspace),
                    llm_responses=responses,
                    tool_results={"manage_conversation_tag": tag_payload},
                    closing_escalation_result=True,
                ),
                fake_send_capi,
                fake_dispatch_event,
            ],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_patrona",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_patrona",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Sí, ese mismo. Confirmado.", None, None],
            )
            await handle.result()

    # 1. Sanity: la red patrón A escaló con la reason del mapa.
    assert [
        (sid, reason) for (sid, reason, _m) in tracker.closing_escalation_calls
    ] == [("wa_patrona", "ORDER_PENDING_SHIPPING_DETAILS")], (
        f"La red patrón A no corrió como esperado: "
        f"{tracker.closing_escalation_calls}"
    )
    # 2. EL BUG: la despedida DEBE llegar al cliente aunque la red escale.
    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert any("un asesor te escribe pronto" in m for m in sent), (
        f"La escalación de la red patrón A suprimió el final_content. "
        f"Enviado: {sent}"
    )
    # 3. El workflow igual escaló y terminó sin ciclo de ghosting.
    assert tracker.ghosting_calls == 0, (
        f"El shutdown por escalación patrón A no cerró el workflow — corrió "
        f"ghosting ({tracker.ghosting_calls} veces)."
    )


@pytest.mark.asyncio
async def test_portavelas_notice_stripped_when_order_has_no_portavelas(
    tmp_path: Path,
) -> None:
    """Run 943e6bff (2026-09-07): el cliente cerró un pedido SIN portavelas y
    recibió "Al finalizar el pago del pedido se escogen los colores del
    portavelas, según disponibilidad". El prompt ya no lo pide, pero el
    guard del workflow (gated `portavelas-notice-guard-v1`) garantiza
    DETERMINISTA que, si `order_registered_decision.portavelas_included` es
    False, ninguna oración sobre el portavelas sale al cliente ni se
    persiste — aunque el LLM la escriba igual."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    order_motivo = (
        "Cliente confirmó pedido order_456 por $17000 COP, método transfer; "
        "falta verificación humana del pago."
    )
    register_payload = json.dumps(
        {
            "registered": True,
            "order_id": "order_456",
            "provider": "medusa",
            "portavelas": {"included": False, "handles": []},
            "order_registered": {
                "session_id": "wa_noportavela",
                "order_id": "order_456",
                "payment_method": "transfer",
                "total_cop": 17000,
                "currency": "COP",
                "motivo": order_motivo,
                "portavelas_included": False,
            },
            "summary": "Pedido registrado en Medusa con ID order_456.",
        },
        ensure_ascii=False,
    )
    responses = [
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_reg",
                    name="register_order",
                    arguments={"confirmado": True},
                )
            ],
        ),
        # El LLM desobedece y mete la frase del portavelas igual (texto
        # literal del incidente).
        LLMResponseData(
            content=(
                "Listo, tu pedido quedó registrado 🤍 Al finalizar el pago "
                "del pedido se escogen los colores del portavelas, según "
                "disponibilidad. Gracias por elegir a Hubara."
            ),
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                tool_results={"register_order": register_payload},
                payment_closure_result=PaymentPendingClosureResult(
                    acted=False, escalated=True
                ),
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_noportavela",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_noportavela",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Sí, confirmo", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert sent, "La despedida no se envió (el guard no debe silenciar el turno)."
    assert all("portavela" not in m.lower() for m in sent), (
        f"La frase del portavelas llegó al cliente en un pedido sin "
        f"portavelas: {sent}"
    )
    assert any("pedido quedó registrado" in m for m in sent), (
        f"El guard borró más de la cuenta — la despedida se perdió: {sent}"
    )
    persisted = [m for (_sid, m) in tracker.persist_calls]
    assert all("portavela" not in m.lower() for m in persisted), (
        f"El historial del dashboard conservó la frase: {persisted}"
    )


@pytest.mark.asyncio
async def test_portavelas_notice_kept_when_order_includes_portavelas(
    tmp_path: Path,
) -> None:
    """Contraparte: con `portavelas_included=True` (Dúo Zodiacal) el aviso al
    comprador es legítimo y el guard NO lo toca."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    register_payload = json.dumps(
        {
            "registered": True,
            "order_id": "order_789",
            "provider": "medusa",
            "portavelas": {"included": True, "handles": ["duo-zodiacal"]},
            "order_registered": {
                "session_id": "wa_duo",
                "order_id": "order_789",
                "payment_method": "transfer",
                "total_cop": 95000,
                "currency": "COP",
                "motivo": "Cliente confirmó pedido order_789; pendiente: color del portavelas.",
                "portavelas_included": True,
            },
            "summary": "Pedido registrado en Medusa con ID order_789.",
        },
        ensure_ascii=False,
    )
    farewell = (
        "Listo, tu pedido quedó registrado 🤍 Al finalizar el pago del pedido "
        "se escogen los colores del portavelas, según disponibilidad. Gracias "
        "por elegir a Hubara."
    )
    responses = [
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_reg",
                    name="register_order",
                    arguments={"confirmado": True},
                )
            ],
        ),
        LLMResponseData(
            content=farewell,
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                tool_results={"register_order": register_payload},
                payment_closure_result=PaymentPendingClosureResult(
                    acted=False, escalated=True
                ),
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_duo",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_duo",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Sí, confirmo", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert any("portavelas" in m for m in sent), (
        f"El guard borró el aviso legítimo de un pedido con portavelas: {sent}"
    )


def _greeting_then_search_resp() -> LLMResponseData:
    """Lo que el LLM hizo en los runs reales (3ce50ef3 / dc32f7fe): saludo
    como content JUNTO a `search_products` — el default-deny lo descarta."""
    return LLMResponseData(
        content=(
            "¡Buenas noches! Bienvenido a *Hubara*, velas artesanales hechas "
            "a base de cera de palma, a mano en Colombia.\n\nDéjame ver qué "
            "tenemos en esa colección."
        ),
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(
                id="c1",
                name="search_products",
                arguments={"q": "amor y amistad", "limit": 10},
            )
        ],
    )


def _present_products_resp(intro_text: str) -> LLMResponseData:
    return LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(
                id="c2",
                name="present_products",
                arguments={
                    "handles": ["cubo-love", "cilindro-love"],
                    "intro_text": intro_text,
                },
            )
        ],
    )


async def _run_ctwa_first_message(
    tracker: Tracker, workspace: Path, *, responses, prior_history=None
) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                prior_history=prior_history,
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_ctwa",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_ctwa",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=[
                    "[el cliente vino desde un anuncio de Facebook/Instagram, "
                    "titulado 'Velas aromáticas']\n¡Hola! Quiero más "
                    "información sobre la colección de amor y amistad",
                    None,
                    None,
                ],
            )
            await handle.result()


@pytest.mark.asyncio
async def test_first_contact_catalog_turn_greets_before_the_menu(
    tmp_path: Path,
) -> None:
    """Sessions run dc32f7fe y run 3ce50ef3 (CTWA "amor y amistad",
    2026-09-10/11): el LLM saludó JUNTO a `search_products` (content
    descartado por el default-deny) y cerró el turno con `present_products`
    → el cliente recibió el menú SIN saludo. Contrato: en el primer contacto
    el workflow garantiza la Burbuja 1 del guion ANTES del flush del menú,
    sin depender de dónde el LLM puso el texto."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_ctwa_first_message(
        tracker,
        workspace,
        responses=[
            _greeting_then_search_resp(),
            _present_products_resp("Estas son nuestras piezas para amor y amistad:"),
        ],
    )

    assert "present_products" in tracker.execute_tool_calls
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert FIRST_CONTACT_GREETING in sent, (
        f"El primer contacto salió sin saludo: sends={sent} "
        f"timeline={tracker.timeline}"
    )
    # El saludo va ANTES del menú (el flush entrega present_products).
    first_flush = tracker.timeline.index("flush")
    assert f"send:{FIRST_CONTACT_GREETING}" in tracker.timeline[:first_flush], (
        f"El saludo no precede al menú: {tracker.timeline}"
    )
    # Y se persiste al dashboard como mensaje del agente.
    assert FIRST_CONTACT_GREETING in [m for (_s, m) in tracker.persist_calls]
    # Una sola vez (el turno ghost no re-saluda).
    assert sent.count(FIRST_CONTACT_GREETING) == 1
    assert tracker.first_contact_greeting_calls == 1


@pytest.mark.asyncio
async def test_returning_customer_catalog_turn_does_not_regreet(
    tmp_path: Path,
) -> None:
    """Cliente que YA conversó (historial con mensajes del agente) y pide el
    catálogo: nada de saludo inyectado — la regla del guion es "si ya hay
    conversación previa, retoma el hilo"."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_ctwa_first_message(
        tracker,
        workspace,
        responses=[
            _tool_resp("search_products"),
            _present_products_resp("Estas son nuestras piezas para amor y amistad:"),
        ],
        prior_history=[
            {"role": "user", "content": "Hola, ¿tienen velas de lavanda?"},
            {"role": "assistant", "content": "¡Buenas tardes! Bienvenido a *Hubara*..."},
        ],
    )

    assert "present_products" in tracker.execute_tool_calls
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert FIRST_CONTACT_GREETING not in sent, (
        f"Re-saludó a un cliente con conversación previa: {sent}"
    )
    assert tracker.first_contact_greeting_calls == 0


@pytest.mark.asyncio
async def test_first_contact_greeting_not_duplicated_when_intro_text_greets(
    tmp_path: Path,
) -> None:
    """Si el LLM puso el saludo en el canal legítimo (`intro_text` de la
    tool), el workflow NO agrega otra burbuja de saludo."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_ctwa_first_message(
        tracker,
        workspace,
        responses=[
            _tool_resp("search_products"),
            _present_products_resp(
                "¡Buenas noches! Bienvenido a *Hubara*. Estas son nuestras "
                "piezas para amor y amistad:"
            ),
        ],
    )

    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert FIRST_CONTACT_GREETING not in sent, (
        f"Saludo duplicado (ya iba en intro_text): {sent}"
    )
    assert tracker.first_contact_greeting_calls == 0


# =============================================================================
# Guarda de enumeración de variantes (run 9bd495be, 2026-09-14)
# =============================================================================

_AROMA_ENUMERATION = (
    "Tenemos 11 aromas disponibles: Caballero de la noche, Limoncillo, Lavanda, "
    "Café, Sándalo, Ylang Ylang, Coco cremoso, Frutos rojos, Verde menta, Drakar "
    "y Chanel.\n\n¿Alguno te llama la atención?"
)


async def _run_single_turn(
    tracker: Tracker, workspace: Path, *, responses, variant_guard_result: bool
) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                variant_guard_result=variant_guard_result,
                prior_history=[
                    {"role": "user", "content": "Hola"},
                    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_enum", runtime_workspace_path=str(workspace)),
                id="session-wa_enum",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=["Opciones", None, None])
            await handle.result()


@pytest.mark.asyncio
async def test_enumerated_aromas_in_text_are_replaced_by_the_picker(tmp_path: Path) -> None:
    """El LLM listó los aromas como texto plano y no llamó al picker: la guarda
    encola el picker (lo entrega el flush) y el texto plano NO se envía."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_single_turn(
        tracker, workspace,
        responses=[_tool_resp("search_products"), _final_resp(_AROMA_ENUMERATION)],
        variant_guard_result=True,
    )

    assert tracker.variant_guard_calls == [_AROMA_ENUMERATION]
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert _AROMA_ENUMERATION not in sent, f"el texto plano salió igual: {sent}"
    assert "flush" in tracker.timeline


@pytest.mark.asyncio
async def test_plain_text_without_enumeration_is_sent_as_usual(tmp_path: Path) -> None:
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_single_turn(
        tracker, workspace,
        responses=[_final_resp("¿Tienes algún aroma en mente?")],
        variant_guard_result=False,
    )

    assert tracker.variant_guard_calls == ["¿Tienes algún aroma en mente?"]
    assert "¿Tienes algún aroma en mente?" in [m for (_s, m) in tracker.send_whatsapp_calls]



# =============================================================================
# HU-SC-0 — traza por turno para el scorecard
# =============================================================================


async def _run_turn_with_tools(
    tracker: Tracker, workspace: Path, *, responses, tool_results, variant_guard_result=False
) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                tool_results=tool_results,
                variant_guard_result=variant_guard_result,
                prior_history=[
                    {"role": "user", "content": "Hola"},
                    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_trace", runtime_workspace_path=str(workspace)),
                id="session-wa_trace",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message, args=["café", None, None]
            )
            await handle.result()


@pytest.mark.asyncio
async def test_turn_trace_records_tools_rejections_narration_and_guard(tmp_path: Path) -> None:
    """Lo que el evaluador viejo no veía: la tool rechazada por su guarda, la
    narración que el default-deny descartó y el texto que la guarda de
    variantes suprimió. Todo queda en la traza del turno; el turno de ghosting
    deja la suya marcada como tal."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    batch = LLMResponseData(
        content="Perfecto, reviso qué tenemos de café",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(id="t1", name="search_products", arguments={"q": "café"}),
            ToolCallData(id="t2", name="escalate_to_human", arguments={"reason_category": "OTHER"}),
        ],
    )

    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[batch, _final_resp(_AROMA_ENUMERATION)],
        tool_results={
            "search_products": json.dumps({"query": "café", "count": 23, "results": []}),
            "escalate_to_human": json.dumps({"escalated": False, "error": "purchase_not_confirmed"}),
        },
        variant_guard_result=True,
    )

    assert [t["trigger"] for t in tracker.turn_traces] == ["customer", "ghost"]
    turn = tracker.turn_traces[0]
    assert turn["inbound_text"] == "café"
    assert [(x["name"], x["ok"], x["error"]) for x in turn["tools"]] == [
        ("search_products", True, None),
        ("escalate_to_human", False, "purchase_not_confirmed"),
    ]
    assert "count:23" in turn["tools"][0]["notes"]
    assert turn["discarded_narration"] == ["Perfecto, reviso qué tenemos de café"]
    assert turn["llm_text"] == _AROMA_ENUMERATION
    assert turn["sent_texts"] == []
    assert turn["suppressed_reason"] == "variant_enumeration_guard"
    assert turn["guards"] == ["variant_enumeration_guard"]
    assert turn["first_contact"] is False
    assert tracker.turn_traces[1]["sent_texts"] == []


@pytest.mark.asyncio
async def test_turn_trace_records_the_text_the_customer_received(tmp_path: Path) -> None:
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[_final_resp("¿Es para ti o para regalo?")],
        tool_results={},
    )

    turn = tracker.turn_traces[0]
    assert turn["sent_texts"] == ["¿Es para ti o para regalo?"]
    assert turn["suppressed_reason"] is None
    assert turn["tools"] == []
    assert turn["turn_started_ms"] > 0


# =============================================================================
# Run 5ed9af2d (2026-09-18) — el relevo delató al bot
# =============================================================================
# Forma exacta del incidente:
#   llm_chat #1 → content="Para 100 unidades te coordino con un colega…" +
#                 tool_call escalate_to_human          (la despedida BUENA)
#   default-deny → ese content se descarta (junto a una tool call)
#   execute_tool → "Escalación registrada… NO generes más respuestas"
#   llm_chat #2  → FORZADO por el loop; el modelo acusa recibo al sistema:
#                 "Listo, la conversación quedó en manos del equipo humano."
#   send         → ese acuse le llegó al cliente.
# Contrato nuevo: la despedida viaja en `customer_message`, la tool devuelve el
# texto final en el envelope y la escalación TERMINA el turno — el llm_chat #2
# no existe, así que no hay acuse que pueda filtrarse.

_RELAY_FAREWELL = (
    "Para 100 unidades te coordino con un colega del equipo, que maneja ese "
    "tipo de pedidos y te responde en este mismo chat 🤍"
)
_RELAY_LEAK = "Listo, la conversación quedó en manos del equipo humano."


def _escalation_batch(customer_message: str | None) -> LLMResponseData:
    args = {
        "reason_category": "BULK_ORDER",
        "summary": "Cliente pide ~100 presentes sencillos; requiere cotización por volumen.",
    }
    if customer_message is not None:
        args["customer_message"] = customer_message
    return LLMResponseData(
        content=_RELAY_FAREWELL,
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[ToolCallData(id="esc1", name="escalate_to_human", arguments=args)],
    )


def _escalation_envelope(customer_message: str) -> str:
    return json.dumps(
        {
            "escalation_decision": {
                "session_id": "wa_trace",
                "reason_category": "BULK_ORDER",
                "summary": "Cliente pide ~100 presentes sencillos",
            },
            "customer_message": customer_message,
            "message": "Hecho: un colega del equipo continúa la atención en este chat.",
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_escalation_sends_the_farewell_and_never_asks_the_llm_for_an_ack(
    tmp_path: Path,
) -> None:
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_turn_with_tools(
        tracker,
        workspace,
        # El 2º response es el acuse del run real: si el loop lo pide, se filtra.
        responses=[_escalation_batch(_RELAY_FAREWELL), _final_resp(_RELAY_LEAK)],
        tool_results={"escalate_to_human": _escalation_envelope(_RELAY_FAREWELL)},
    )

    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert sent == [_RELAY_FAREWELL], f"el cliente recibió: {sent}"
    assert tracker.llm_calls == 1, "la escalación termina el turno: sin llm_chat de acuse"
    turn = tracker.turn_traces[0]
    assert turn["sent_texts"] == [_RELAY_FAREWELL]
    assert turn["llm_text"] == _RELAY_FAREWELL


@pytest.mark.asyncio
async def test_escalation_without_farewell_in_envelope_sends_nothing_and_asks_no_ack(
    tmp_path: Path,
) -> None:
    """Defensa: un envelope de escalación SIN `customer_message` (tool ajena o
    worker viejo) termina el turno en silencio — jamás se vuelve a abrir el
    canal del acuse pidiéndole otro texto al LLM."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    legacy_envelope = json.dumps(
        {
            "escalation_decision": {
                "session_id": "wa_trace",
                "reason_category": "BULK_ORDER",
                "summary": "Cliente pide ~100 presentes sencillos",
            },
            "message": "Escalación registrada.",
        },
        ensure_ascii=False,
    )

    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[_escalation_batch(None), _final_resp(_RELAY_LEAK)],
        tool_results={"escalate_to_human": legacy_envelope},
    )

    assert tracker.send_whatsapp_calls == []
    assert tracker.llm_calls == 1


@pytest.mark.asyncio
async def test_tag_tool_ack_in_a_customer_turn_is_not_sent(tmp_path: Path) -> None:
    """Run b06636a6: tras `manage_conversation_tag` el llm_chat forzado devolvió
    "Etiqueta registrada.". Ahí no salió por ser turno de cierre (admin); en un
    turno NORMAL (el cliente dice "no gracias") era `final_content` y ningún
    patrón lo frenaba. En runs nuevos el set extendido
    (`admin-leak-patterns-v2`) lo bloquea y la traza deja constancia."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[_tool_resp("manage_conversation_tag"), _final_resp("Etiqueta registrada.")],
        tool_results={
            "manage_conversation_tag": json.dumps(
                {"message": "Éxito. Interacción etiquetada como 'RECHAZO'."},
                ensure_ascii=False,
            )
        },
    )

    assert "Etiqueta registrada." not in [m for (_s, m) in tracker.send_whatsapp_calls]
    turn = tracker.turn_traces[0]
    assert turn["suppressed_reason"] == "admin_text_guard"
    assert turn["guards"] == ["admin_text_guard"]


@pytest.mark.asyncio
async def test_incident_shape_with_the_real_tool_sends_the_approved_farewell(
    tmp_path: Path,
) -> None:
    """Contrato tool↔loop de punta a punta con la forma EXACTA del run 5ed9af2d:
    el LLM llama `escalate_to_human` SIN `customer_message` (schema viejo) y con
    la despedida como content. El envelope lo produce la tool REAL de Sales
    (guarda + plataforma): si alguien renombra la clave `customer_message` en un
    solo lado, este test lo caza."""
    from exoclaw.agent.tools import ToolContext

    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    vault = tmp_path / "vault"
    vault.mkdir()
    tool = guarded_escalation_tool(EscalateToHumanTool)(
        workspace=str(tmp_path), vault_dir=vault
    )
    real_envelope = await tool.execute_with_context(
        ToolContext(session_key="wa_trace", channel="whatsapp", chat_id="wa_trace"),
        reason_category="BULK_ORDER",
        summary="Cliente pide ~100 presentes sencillos; requiere cotización por volumen.",
    )

    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[_escalation_batch(None), _final_resp(_RELAY_LEAK)],
        tool_results={"escalate_to_human": real_envelope},
    )

    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert sent == ["Un colega del equipo te responde en este mismo chat 🤍"]
    assert tracker.llm_calls == 1


@pytest.mark.asyncio
async def test_farewell_survives_a_variant_picker_in_the_same_batch(tmp_path: Path) -> None:
    """Un batch [present_variant_picker, escalate_to_human] activaba
    `suppress_text_for_picker` ("el picker YA es el mensaje") y la despedida del
    relevo no salía: el cliente quedaba escalado y sin una palabra."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    batch = LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(id="p1", name="present_variant_picker", arguments={"handle": "cubo-love"}),
            ToolCallData(
                id="esc1",
                name="escalate_to_human",
                arguments={
                    "reason_category": "BULK_ORDER",
                    "summary": "pide ~100 unidades",
                    "customer_message": _RELAY_FAREWELL,
                },
            ),
        ],
    )

    await _run_turn_with_tools(
        tracker,
        workspace,
        responses=[batch, _final_resp(_RELAY_LEAK)],
        tool_results={
            "present_variant_picker": json.dumps({"queued": True}),
            "escalate_to_human": _escalation_envelope(_RELAY_FAREWELL),
        },
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_RELAY_FAREWELL]
    assert tracker.llm_calls == 1


# =============================================================================
# El tag AUTOSUFICIENTE termina el turno — run b06636a6 (2026-09-18)
# =============================================================================
# Misma clase que L-20, otra tool. Cadena real del cierre por ghosting:
#   llm_chat #1  → manage_conversation_tag(INTERESADO)
#   tool result  → "Éxito. Interacción etiquetada como 'INTERESADO'."
#   llm_chat #2  → FORZADO por el loop; el modelo acusa recibo: "Etiqueta registrada."
# No llegó al cliente SOLO por ser turno admin, pero (1) `record_turn` lo
# persistió como `assistant` → few-shot de "tras una tool se acusa recibo" en el
# contexto que produjo el leak del run 5ed9af2d; (2) en un turno de CLIENTE era
# el final_content; (3) ~55K prompt tokens por cierre para un texto descartado.
# Contrato nuevo: la tool DECLARA el cierre (`tag_closure`) y el loop corta.

_TAG_ACK = "Etiqueta registrada."
_CLOSING_LINE = "Con gusto, aquí estaré por si más adelante te animas 🤍"
_FIRST_REPLY = "¿Qué aroma te gustaría?"
_ABSENT = object()


def _tag_batch(
    tag: str, *, customer_message: str | None = None, content: str = ""
) -> LLMResponseData:
    args = {"tag": tag, "motivo": "cierre"}
    if customer_message is not None:
        args["customer_message"] = customer_message
    return LLMResponseData(
        content=content,
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[ToolCallData(id="tag1", name="manage_conversation_tag", arguments=args)],
    )


def _tag_envelope(
    tag: str,
    *,
    ends_turn: bool = True,
    customer_message: object = _ABSENT,
    episode_closed: bool = False,
) -> str:
    closure: dict = {"tag": tag, "ends_turn": ends_turn}
    if customer_message is not _ABSENT:
        closure["customer_message"] = customer_message
    payload: dict = {"message": "Hecho.", "tag_closure": closure}
    if episode_closed:
        payload["episode_closed"] = {
            "session_id": "wa_tagturn",
            "episode_id": "ep_007",
            "closing_tag": tag,
        }
    return json.dumps(payload, ensure_ascii=False)


async def _run_tag_session(
    tracker: Tracker,
    workspace: Path,
    *,
    responses: list[LLMResponseData],
    tool_results: dict[str, str],
    closing_escalation_result: bool = False,
    customer_text: str = "Hola, busco una vela",
) -> None:
    """UN mensaje del cliente y después silencio: el workflow corre el turno del
    cliente y, al vencer el idle, el turno ADMIN de cierre por ghosting."""

    # El cierre de episodio dispara EpisodeClosedEvent + CAPI (mismos fakes
    # locales que `test_closing_escalation_safety_net_…`).
    @activity.defn(name="send_capi_event_activity")
    async def fake_send_capi(session_id: str, episode_id: str, event_name: str) -> dict:
        return {"status": "sent", "event_id": f"close_{episode_id}", "event_name": event_name}

    @activity.defn(name="orchestration.dispatch_event")
    async def fake_dispatch_event(envelope) -> dict:
        return {
            "source_plugin": "chats",
            "source_worker": "sales",
            "event_type": "chats.episode_closed",
        }

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=[
                *_make_fake_activities(
                    tracker,
                    workspace_path=str(workspace),
                    llm_responses=responses,
                    tool_results=tool_results,
                    closing_escalation_result=closing_escalation_result,
                    prior_history=[
                        {"role": "user", "content": "Hola"},
                        {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                    ],
                ),
                fake_send_capi,
                fake_dispatch_event,
            ],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_tagturn", runtime_workspace_path=str(workspace)),
                id="session-wa_tagturn",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message, args=[customer_text, None, None]
            )
            await handle.result()


def _assistant_texts(new_messages: list[dict]) -> list[str]:
    """Lo que el LLM va a RECORDAR haberle dicho al cliente: los mensajes
    assistant de TEXTO. El content que acompaña a una tool call (narración que
    el default-deny descarta) es otra discusión y no se mide acá."""
    return [
        m["content"]
        for m in new_messages
        if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")
    ]


# ------------------------------------------------------------- A · turno admin


@pytest.mark.asyncio
async def test_ghost_close_tag_ends_the_turn_and_never_asks_for_an_ack(
    tmp_path: Path,
) -> None:
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        # El 3er response es el acuse del run real: si el loop lo pide, existe.
        responses=[_final_resp(_FIRST_REPLY), _tag_batch("INTERESADO"), _final_resp(_TAG_ACK)],
        tool_results={"manage_conversation_tag": _tag_envelope("INTERESADO")},
    )

    assert tracker.ghosting_calls == 1
    assert tracker.execute_tool_calls == ["manage_conversation_tag"]
    assert tracker.llm_calls == 2, (
        "turno del cliente + turno de cierre: el tag termina el turno, sin el "
        f"llm_chat del acuse (hubo {tracker.llm_calls})"
    )
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_FIRST_REPLY]
    ghost_turn = tracker.record_turn_new_messages[-1]
    assert _assistant_texts(ghost_turn) == [], f"el LLM recordaría: {ghost_turn}"


@pytest.mark.asyncio
async def test_ghost_close_combo_tag_still_lets_the_llm_escalate(tmp_path: Path) -> None:
    """CONFIRMADO_SIN_DATOS va en combo con `escalate_to_human`: ahí el `llm_chat`
    siguiente SÍ tiene trabajo (escalar con su resumen para el colega). Cortar por
    NOMBRE de tool le quitaría al relevo de la venta más valiosa el resumen del
    modelo y lo dejaría con el motivo genérico de la red de seguridad. La
    despedida de la escalación no sale (el cliente ya no está) ni se recuerda."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        responses=[
            _final_resp(_FIRST_REPLY),
            _tag_batch("CONFIRMADO_SIN_DATOS"),
            _escalation_batch(_RELAY_FAREWELL),
            _final_resp(_RELAY_LEAK),
        ],
        tool_results={
            "manage_conversation_tag": _tag_envelope("CONFIRMADO_SIN_DATOS", ends_turn=False),
            "escalate_to_human": _escalation_envelope(_RELAY_FAREWELL),
        },
    )

    assert tracker.execute_tool_calls == ["manage_conversation_tag", "escalate_to_human"]
    assert tracker.llm_calls == 3, "cliente + (tag, escalate); sin acuse tras escalar"
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_FIRST_REPLY]
    ghost_turn = tracker.record_turn_new_messages[-1]
    assert _assistant_texts(ghost_turn) == [], (
        f"despedida NO enviada (turno admin) pero recordada: {ghost_turn}"
    )


@pytest.mark.asyncio
async def test_ghost_close_combo_tag_without_escalation_is_covered_by_the_safety_net(
    tmp_path: Path,
) -> None:
    """El LLM marca CONFIRMADO_SIN_DATOS y, en vez de escalar, acusa recibo. La
    red `ensure_closing_escalation` escala por él; el acuse ni sale ni se recuerda."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        responses=[_final_resp(_FIRST_REPLY), _tag_batch("CONFIRMADO_SIN_DATOS"), _final_resp(_TAG_ACK)],
        tool_results={
            "manage_conversation_tag": _tag_envelope(
                "CONFIRMADO_SIN_DATOS", ends_turn=False, episode_closed=True
            )
        },
        closing_escalation_result=True,
    )

    assert [(s, r) for (s, r, _m) in tracker.closing_escalation_calls] == [
        ("wa_tagturn", "ORDER_PENDING_SHIPPING_DETAILS")
    ]
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_FIRST_REPLY]
    ghost_turn = tracker.record_turn_new_messages[-1]
    assert _assistant_texts(ghost_turn) == [], f"el LLM recordaría: {ghost_turn}"


# ---------------------------------------------------------- B · turno de cliente


@pytest.mark.asyncio
async def test_closing_tag_in_a_customer_turn_sends_the_customer_message_and_asks_no_ack(
    tmp_path: Path,
) -> None:
    """El cliente dice "no gracias": el modelo escribe la despedida como content
    JUNTO a la tool call (se descarta, default-deny) y la repite en
    `customer_message`. Sale ESA, una sola vez, y no hay llm_chat de acuse."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="No gracias, ya no me interesa",
        responses=[
            _tag_batch("RECHAZO", customer_message=_CLOSING_LINE, content=_CLOSING_LINE),
            _final_resp(_TAG_ACK),
        ],
        tool_results={
            "manage_conversation_tag": _tag_envelope("RECHAZO", customer_message=_CLOSING_LINE)
        },
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_CLOSING_LINE]
    customer_turn = tracker.record_turn_new_messages[0]
    assert _assistant_texts(customer_turn)[-1] == _CLOSING_LINE, (
        "lo que SÍ salió queda en el historial del LLM"
    )
    trace = tracker.turn_traces[0]
    assert trace["sent_texts"] == [_CLOSING_LINE]
    assert trace["llm_text"] == _CLOSING_LINE
    # Turno del cliente (1) + cierre por ghosting posterior (1, que también corta).
    assert tracker.llm_calls == 2, f"hubo un llm_chat de acuse: {tracker.llm_calls}"


@pytest.mark.asyncio
async def test_closing_tag_without_customer_message_keeps_the_llm_reply(
    tmp_path: Path,
) -> None:
    """Sin `customer_message` (sesión en vuelo con el schema viejo, o el modelo
    prefiere responder aparte) el cliente SIGUE esperando respuesta: ese llm_chat
    es legítimo y su texto sale. Cortar acá dejaría al cliente en silencio."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="Lo voy a pensar. ¿Hacen envíos a Cali?",
        responses=[_tag_batch("INTERESADO"), _final_resp("Sí, enviamos a Cali 🤍")],
        tool_results={"manage_conversation_tag": _tag_envelope("INTERESADO")},
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == ["Sí, enviamos a Cali 🤍"]


@pytest.mark.asyncio
async def test_closing_tag_with_nothing_safe_to_say_ends_the_turn_in_silence(
    tmp_path: Path,
) -> None:
    """El modelo mandó `customer_message` pero era un parte interno: la tool lo
    declara VACÍO (≠ ausente). El modelo ya mostró que confunde al destinatario:
    el turno termina callado en vez de reabrirle el canal con otro llm_chat."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="No gracias",
        responses=[_tag_batch("RECHAZO", customer_message=_TAG_ACK), _final_resp(_TAG_ACK)],
        tool_results={"manage_conversation_tag": _tag_envelope("RECHAZO", customer_message="")},
    )

    assert tracker.send_whatsapp_calls == []
    assert tracker.llm_calls == 2, "turno del cliente (1) + cierre por ghosting (1)"


# ------------------------------------- C · lo que no salió, el LLM no lo recuerda


@pytest.mark.asyncio
async def test_blocked_ack_is_not_remembered_by_the_llm(tmp_path: Path) -> None:
    """Tool result de forma vieja (sin `tag_closure`): el loop no corta, el modelo
    acusa recibo y el tripwire lo bloquea. Antes ese texto igual se persistía con
    `record_turn`: la sesión siguiente lo leía como algo que "ya dijo"."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="No gracias",
        responses=[_tag_batch("RECHAZO"), _final_resp(_TAG_ACK)],
        tool_results={
            "manage_conversation_tag": json.dumps(
                {"message": "Éxito. Interacción etiquetada como 'RECHAZO'."},
                ensure_ascii=False,
            )
        },
    )

    assert tracker.send_whatsapp_calls == []
    customer_turn = tracker.record_turn_new_messages[0]
    assert _assistant_texts(customer_turn) == [], f"el LLM recordaría: {customer_turn}"
    # El turno en sí NO desaparece: lo que el cliente dijo y la tool quedan.
    assert [m.get("role") for m in customer_turn] == ["user", "assistant", "tool"]


@pytest.mark.asyncio
async def test_admin_turn_prose_is_not_remembered_by_the_llm(tmp_path: Path) -> None:
    """Run 5f43bcd0 (llm_chat 729): en el cierre por ghosting el modelo desobedece
    y escribe el resumen sin llamar la tool. No sale (turno admin) — y tampoco
    debe quedar como algo que el agente le dijo al cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        responses=[_final_resp(_FIRST_REPLY), _final_resp("Quedó pensándolo, vuelvo luego.")],
        tool_results={},
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_FIRST_REPLY]
    ghost_turn = tracker.record_turn_new_messages[-1]
    assert _assistant_texts(ghost_turn) == [], f"el LLM recordaría: {ghost_turn}"
    # Y tampoco queda el TRIGGER solo. Sin la prosa que lo "cerraba", la sesión
    # siguiente leería [user: "[SISTEMA]… NO generes ninguna respuesta visible…"]
    # [user: mensaje nuevo del cliente] y podría aplicarle la orden vieja al
    # mensaje nuevo (cliente que vuelve y recibe silencio). Un turno admin sin
    # tool calls no dejó ningún hecho que recordar: "nunca pasó".
    assert ghost_turn == [], f"quedó una instrucción de sistema sin cerrar: {ghost_turn}"
    assert tracker.record_turn_calls == 2, "record_turn se agenda SIEMPRE (misma forma de history)"


@pytest.mark.asyncio
async def test_no_message_abstention_is_still_remembered(tmp_path: Path) -> None:
    """Decisión deliberada: `NO_MESSAGE` NO se recorta. No es un acuse sino el
    canal CORRECTO de abstención; que el modelo vea que lo usó es el few-shot
    bueno (borrarlo empuja a declinar en prosa, que es lo que se filtraba)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                pending_handoff="Usuario respondió: (sin mensaje nuevo)",
                llm_responses=[_final_resp("NO_MESSAGE")],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_tagturn", runtime_workspace_path=str(workspace)),
                id="session-wa_tagturn",
                task_queue=SALES_QUEUE,
            )
            await handle.result()

    assert tracker.send_whatsapp_calls == []
    assert _assistant_texts(tracker.record_turn_new_messages[0]) == ["NO_MESSAGE"]


class _SequencedResults(dict):
    """`tool_results` donde una tool devuelve un envelope DISTINTO por llamada
    (valor = lista, se consume en orden); un valor `str` se repite siempre."""

    def __getitem__(self, key: str) -> str:
        value = super().__getitem__(key)
        return value.pop(0) if isinstance(value, list) else value


@pytest.mark.asyncio
async def test_the_last_tag_of_the_batch_decides_whether_the_turn_ends(tmp_path: Path) -> None:
    """Dos tags en UN batch (el modelo se corrige): en metadata gana la última
    escritura, así que el cierre que vale es el de la ÚLTIMA llamada. Si la
    primera era autosuficiente y la última es combo, el turno NO termina: el
    modelo todavía tiene que escalar."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    two_tags = LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(
                id="t1",
                name="manage_conversation_tag",
                arguments={"tag": "RECHAZO", "motivo": "primer intento"},
            ),
            ToolCallData(
                id="t2",
                name="manage_conversation_tag",
                arguments={"tag": "CONFIRMADO_SIN_DATOS", "motivo": "se corrige"},
            ),
        ],
    )

    await _run_tag_session(
        tracker,
        workspace,
        responses=[_final_resp(_FIRST_REPLY), two_tags, _escalation_batch(_RELAY_FAREWELL)],
        tool_results=_SequencedResults(
            {
                "manage_conversation_tag": [
                    _tag_envelope("RECHAZO"),
                    _tag_envelope("CONFIRMADO_SIN_DATOS", ends_turn=False),
                ],
                "escalate_to_human": _escalation_envelope(_RELAY_FAREWELL),
            }
        ),
    )

    assert tracker.execute_tool_calls == [
        "manage_conversation_tag",
        "manage_conversation_tag",
        "escalate_to_human",
    ], "el turno se cortó en el primer tag y el modelo nunca llegó a escalar"


@pytest.mark.asyncio
async def test_real_tag_tool_envelope_ends_the_turn_end_to_end(tmp_path: Path) -> None:
    """Contrato tool↔loop de punta a punta: los envelopes los produce la tool REAL
    (los demás tests usan envelopes escritos a mano). Si alguien renombra
    `tag_closure` / `ends_turn` / `customer_message` en UN solo lado, este test lo
    caza. Cubre los dos sitios del corte: "no gracias" del cliente y el cierre
    por ghosting posterior."""
    from exoclaw.agent.tools import ToolContext

    from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool

    vault = tmp_path / "vault"
    vault.mkdir()
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=vault)
    ctx = ToolContext(session_key="wa_tagturn", channel="whatsapp", chat_id="wa_tagturn")
    customer_turn_envelope = await tool.execute_with_context(
        ctx, tag="RECHAZO", motivo="dijo que no", customer_message=_CLOSING_LINE
    )
    ghost_turn_envelope = await tool.execute_with_context(
        ctx, tag="RECHAZO", motivo="dejó de responder"
    )

    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    await _run_tag_session(
        tracker,
        workspace,
        customer_text="No gracias, ya no me interesa",
        responses=[
            _tag_batch("RECHAZO", customer_message=_CLOSING_LINE, content=_CLOSING_LINE),
            _tag_batch("RECHAZO"),
            _final_resp(_TAG_ACK),
        ],
        tool_results=_SequencedResults(
            {"manage_conversation_tag": [customer_turn_envelope, ghost_turn_envelope]}
        ),
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_CLOSING_LINE]
    assert tracker.execute_tool_calls == ["manage_conversation_tag"] * 2
    assert tracker.llm_calls == 2, (
        "un llm_chat por turno: ni el turno del cliente ni el cierre por "
        f"ghosting piden el acuse (hubo {tracker.llm_calls})"
    )
    remembered = [t for turn in tracker.record_turn_new_messages for t in _assistant_texts(turn)]
    assert remembered == [_CLOSING_LINE], f"el LLM recordaría: {remembered}"


@pytest.mark.asyncio
async def test_a_picker_in_the_same_batch_wins_over_the_closing_tag(tmp_path: Path) -> None:
    """Batch contradictorio [present_variant_picker, tag + customer_message]: el
    picker le PREGUNTA algo al cliente y el tag se DESPIDE. Gana el corte L-11 (el
    picker ES el mensaje, como antes de este cambio): la despedida no sale, no
    aparece en el dashboard como si hubiera salido y el LLM no la recuerda.
    (Con la escalación es al revés —la despedida sobrevive— porque es definitiva.)"""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    batch = LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(id="p1", name="present_variant_picker", arguments={"handle": "cubo-love"}),
            ToolCallData(
                id="tag1",
                name="manage_conversation_tag",
                arguments={"tag": "INTERESADO", "motivo": "duda", "customer_message": _CLOSING_LINE},
            ),
        ],
    )

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="¿Qué aromas hay?",
        responses=[batch, _final_resp(_TAG_ACK)],
        tool_results={
            "present_variant_picker": json.dumps({"queued": True}),
            "manage_conversation_tag": _tag_envelope("INTERESADO", customer_message=_CLOSING_LINE),
        },
    )

    assert tracker.send_whatsapp_calls == [], "el picker es el único mensaje del turno"
    assert tracker.persist_calls == [], "el dashboard mostraría un texto que el cliente no recibió"
    assert _assistant_texts(tracker.record_turn_new_messages[0]) == []
    assert "flush" in tracker.timeline, "el picker sí se entrega"


@pytest.mark.asyncio
async def test_a_failed_tool_in_the_batch_keeps_the_turn_open(tmp_path: Path) -> None:
    """Si ALGUNA tool del batch falló, el modelo tiene que verlo: no se corta.
    Caso: dos tags en un batch y el último rebota por precondición (su envelope de
    error no trae `tag_closure`) — sin esto sobrevivía el cierre del primero y el
    turno terminaba con el error sin leer."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    two_tags = LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(
                id="t1",
                name="manage_conversation_tag",
                arguments={"tag": "RECHAZO", "motivo": "x", "customer_message": _CLOSING_LINE},
            ),
            ToolCallData(
                id="t2",
                name="manage_conversation_tag",
                arguments={"tag": "CONFIRMADO_PAGO_PENDIENTE", "motivo": "y"},
            ),
        ],
    )

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="Listo, ya pagué",
        responses=[two_tags, _final_resp("Dame un momento y lo reviso 🤍")],
        tool_results=_SequencedResults(
            {
                "manage_conversation_tag": [
                    _tag_envelope("RECHAZO", customer_message=_CLOSING_LINE),
                    json.dumps({"error": "precondition_failed: falta register_order"}),
                ]
            }
        ),
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == ["Dame un momento y lo reviso 🤍"]


# =============================================================================
# El corte L-11 exige que la tool haya MOSTRADO algo — runs 01a0caec / 01a0cb16
# =============================================================================
# El corte de turno (L-11, run b730c006) existe porque el cliente ya tiene algo
# delante que responder: seguir iterando deja al modelo "responderse a sí
# mismo". Pero el corte decidía por el NOMBRE de la tool. Cuando la tool se
# NIEGA (`queued: false` — "No se envió nada"), la premisa es falsa: el cliente
# no recibió nada y el modelo nunca lee el rechazo → el bot se queda callado.
#   01a0caec: send_quick_replies → catalog_choice_not_allowed → silencio →
#             ghosting cerró INTERESADO, tomó el humano.
#   01a0cb16: carrito → request_shipping_details → purchase_not_confirmed →
#             silencio con el pedido armado.

_QR_REJECTED = json.dumps({
    "queued": False,
    "error": "catalog_choice_not_allowed",
    "message": "send_quick_replies NO sirve para elegir aromas. No se envió nada. "
    "Usa present_variant_picker.",
}, ensure_ascii=False)
_SHIPPING_REJECTED = json.dumps({
    "queued": False,
    "error": "purchase_not_confirmed",
    "message": "El cliente todavía NO confirmó. Dile el precio y pregúntale si lo "
    "dejamos así (send_quick_replies). No se mostró nada al cliente.",
}, ensure_ascii=False)
_QUEUED = json.dumps({"queued": True, "kind": "quick_replies", "count": 2})


def _customer_turn_tool_calls(tracker: Tracker) -> list[str]:
    """Tools que el modelo llamó en el turno del CLIENTE (el primero grabado)."""
    names: list[str] = []
    for m in tracker.record_turn_new_messages[0]:
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            names.append((fn or {}).get("name") or tc.get("name"))
    return names


@pytest.mark.asyncio
async def test_a_rejected_turn_ending_tool_lets_the_model_answer(tmp_path: Path) -> None:
    """Run 01a0caec: los quick replies rebotan → el modelo lee el rechazo y
    responde. El cliente recibe ESA respuesta en vez de silencio."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    reply = "Tenemos aromas frescos y cálidos 🤍 ¿Cuál te gusta más para tu casa?"

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="Es para mí y para regalar, me encanta que huela rico",
        responses=[_tool_resp("send_quick_replies"), _final_resp(reply)],
        tool_results={"send_quick_replies": _QR_REJECTED},
    )

    assert reply in [m for (_s, m) in tracker.send_whatsapp_calls], (
        f"el rechazo cortó el turno y el cliente no recibió nada: {tracker.send_whatsapp_calls}"
    )


@pytest.mark.asyncio
async def test_a_rejected_shipping_form_lets_the_model_ask_and_then_cuts(
    tmp_path: Path,
) -> None:
    """Run 01a0cb16: el formulario rebota → el modelo sigue la instrucción
    (quick replies de confirmación), ESOS sí salen y AHÍ se corta: no hay un
    tercer llm_chat en el turno del cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="[el cliente armó un carrito con: 1× HUB-TRILOGIA]",
        responses=[
            _tool_resp("request_shipping_details"),
            _tool_resp("send_quick_replies"),
            _tool_resp("set_order_slot"),
        ],
        tool_results={
            "request_shipping_details": _SHIPPING_REJECTED,
            "send_quick_replies": _QUEUED,
        },
    )

    assert _customer_turn_tool_calls(tracker) == [
        "request_shipping_details",
        "send_quick_replies",
    ]


@pytest.mark.asyncio
async def test_a_delivered_picker_still_cuts_even_if_another_tool_failed(
    tmp_path: Path,
) -> None:
    """Regresión b730c006: el selector SÍ salió → el cliente tiene la pregunta
    delante. Aunque otra tool del batch falle, el turno corta: el modelo no
    puede elegir el color por el cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    await _run_tag_session(
        tracker,
        workspace,
        customer_text="Quiero el cubo love",
        responses=[
            _tool_resp("present_variant_picker", "set_order_slot"),
            _tool_resp("set_order_slot"),
        ],
        tool_results={
            "present_variant_picker": json.dumps({"queued": True, "kind": "variant_picker"}),
            "set_order_slot": json.dumps({"updated": False, "error": "invalid_slot"}),
        },
    )

    assert _customer_turn_tool_calls(tracker) == ["present_variant_picker", "set_order_slot"], (
        "el modelo siguió tras mostrar el selector — puede responder por el cliente"
    )


@pytest.mark.asyncio
async def test_leaked_deliberation_paragraph_is_dropped_and_the_answer_is_sent(
    tmp_path: Path,
) -> None:
    """Run edbb0d8b (2026-09-22): el cliente escribió "Me gusta"; el LLM
    respondió con su razonamiento como primer párrafo ("El cliente dice… Le
    respondo…") y la respuesta real después. `admin_text_guard` bloqueó el
    texto ENTERO y el cliente quedó sin respuesta. Contrato: se cae solo el
    párrafo filtrado; la respuesta limpia sale y se persiste."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    deliberation = (
        'El cliente dice "Me gusta" sin más contexto. Está retomando tras el '
        "remarketing. Le respondo de forma natural."
    )
    answer = "¿Qué fue lo que más te gustó? 🤍 Te muestro la promo con tu cupón."

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[_final_resp(f"{deliberation}\n\n{answer}")],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_salvage",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_salvage",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Me gusta", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert answer in sent, f"la respuesta limpia debió salir: {sent}"
    assert not any("El cliente dice" in m for m in sent), sent
    persisted = [m for (_sid, m) in tracker.persist_calls]
    assert answer in persisted
    assert not any("El cliente dice" in m for m in persisted), persisted


@pytest.mark.asyncio
async def test_the_llm_remembers_exactly_the_salvaged_answer_it_sent(
    tmp_path: Path,
) -> None:
    """Run 28a8e407 (2026-09-23, 15:35–15:38): el rescate mandaba "No, el
    cupón aplica solo a… ¿Quieres que te muestre esas cuatro?", pero el
    historial del LLM se grababa ANTES y sin esa respuesta (el texto crudo
    olía a razonamiento → "lo que no sale no se recuerda"). El LLM veía
    "¿Todos tienen cupón?" → "Si" → "Si" → "Si" sin nada en medio y contestaba
    lo mismo tres veces. Contrato: lo que el LLM recuerda es EXACTAMENTE lo
    que recibió el cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    deliberation = (
        "El cliente pregunta si todos los productos tienen cupón. El cupón "
        "aplica solo a productos específicos."
    )
    answer = (
        "No, el cupón aplica solo a las piezas de Amor y Amistad 🤍\n\n"
        "¿Quieres que te muestre esas cuatro?"
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[_final_resp(f"{deliberation}\n\n{answer}")],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_salvage_record",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_salvage_record",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Todos estos productos tiene cupón ?", None, None],
            )
            await handle.result()

    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert sent == [answer], sent
    customer_turn = tracker.record_turn_new_messages[0]
    remembered = [
        m.get("content") for m in customer_turn if m.get("role") == "assistant"
    ]
    assert remembered == [answer], (
        f"el LLM debe recordar lo que el cliente recibió: {customer_turn}"
    )
    assert not any(
        "El cliente pregunta" in str(m.get("content")) for m in customer_turn
    ), customer_turn


@pytest.mark.asyncio
async def test_each_turn_aligns_the_llm_history_with_the_episode_before_the_prompt(
    tmp_path: Path,
) -> None:
    """Runs edbb0d8b / 8e73b7dc: el historial del LLM es por sesión y el
    episodio que abrió la campaña seguía viendo la Trilogía. Antes de armar el
    prompt, el turno le pide a platform que corte el historial de ESTE agente
    si el episodio activo lo pide (la activity decide; acá solo el cableado)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=[_final_resp("¡Hola! ¿Qué te gustó de la promo?")],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_reset",
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_reset",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Me gusta", None, None],
            )
            await handle.result()

    assert tracker.history_reset_calls
    assert set(tracker.history_reset_calls) == {str(workspace)}
    first_send = next(i for i, e in enumerate(tracker.timeline) if e.startswith("send:"))
    assert tracker.timeline.index("reset_llm_history") < first_send
    assert len(tracker.history_reset_calls) == len(tracker.build_prompt_calls)
