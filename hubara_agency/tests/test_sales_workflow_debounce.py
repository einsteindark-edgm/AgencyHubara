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

from src.platform.plugin_manifest import get_task_queue
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
        self.ghosting_calls: int = 0
        self.start_sales_calls: int = 0


def _make_fake_activities(
    tracker: Tracker,
    *,
    workspace_path: str,
    pending_handoff: str | None = None,
    handoff_sequence: list[str | None] | None = None,
    llm_responses: list[LLMResponseData] | None = None,
    tool_results: dict[str, str] | None = None,
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

    @activity.defn(name="read_idle_timeout_seconds")
    async def fake_read_idle_timeout(session_id: str) -> int:
        return 60

    @activity.defn(name="flush_pending_ui_intents_activity")
    async def fake_flush_ui_intents(session_id: str) -> int:
        return 0

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        tracker.typing_calls.append(session_id)

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input: BuildPromptInput) -> list[dict]:
        tracker.build_prompt_calls.append(input)
        return [
            {"role": "system", "content": "fake-system"},
            {"role": "user", "content": input.message},
        ]

    # Secuencia opcional de respuestas LLM (para escenarios multi-iteración como
    # "content + tool_call" seguido de "content final"). Si se agota o no se
    # pasa, devuelve un cierre neutro sin tools (cubre el turno ghost).
    _llm_state = {"i": 0}

    @activity.defn(name="llm_chat")
    async def fake_llm(input: LLMChatInput) -> LLMResponseData:
        tracker.llm_calls += 1
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
        if tool_results and input.name in tool_results:
            return tool_results[input.name]
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input: RecordTurnInput) -> None:
        tracker.record_turn_calls += 1

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send_whatsapp(session_id: str, message: str) -> None:
        tracker.send_whatsapp_calls.append((session_id, message))

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(
        session_id: str, message: str, tools_used: list[str] | None = None
    ) -> None:
        tracker.persist_calls.append((session_id, message))

    @activity.defn(name="decide_ghosting_action")
    async def fake_ghosting() -> str:
        tracker.ghosting_calls += 1
        return "[GHOST] auto-tagging"

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

    return [
        fake_bootstrap,
        fake_read_handoff,
        fake_read_idle_timeout,
        fake_flush_ui_intents,
        fake_typing,
        fake_build_prompt,
        fake_llm,
        fake_execute_tool,
        fake_record_turn,
        fake_send_whatsapp,
        fake_persist,
        fake_ghosting,
        fake_start_sales,
        fake_schedule_remarketing,
        fake_get_active_episode_id,
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
async def test_pre_tool_content_is_sent_as_bubble(tmp_path: Path) -> None:
    """Bug saludo descartado (run ddd0d472) + corte de turno L-11 (run b730c006).

    Cuando el LLM emite texto client-facing JUNTO con una tool call (el saludo
    de apertura "Buenos días. Bienvenido a Hubara..." encolando send_quick_replies),
    ese texto DEBE llegar al cliente como burbuja — antes se perdía porque el
    loop solo enviaba `final_content` (el content del último mensaje SIN tools).

    Contrato L-11: `send_quick_replies` es TURN-ENDING — tras ejecutarla el
    loop corta SIN volver a llamar al LLM (los botones ya invitan a responder;
    un follow-up sería redundante y abría la puerta a que el modelo "siguiera
    solo", como en b730c006). El cliente recibe el saludo y nada más.
    """
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    greeting = "Buenos dias. Bienvenido a Hubara, velas artesanales hechas a mano."
    responses = [
        # Turno user, iter única: saludo + tool call terminal (send_quick_replies).
        # L-11: NO hay iter 2 — el corte de turno evita la segunda llamada LLM.
        LLMResponseData(
            content=greeting,
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_1",
                    name="send_quick_replies",
                    arguments={"body": "¿En qué te ayudo?", "buttons": []},
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
    # El saludo (content que acompañaba la tool call) debe haber llegado, y
    # PRIMERO (es la burbuja del turno del usuario).
    assert sent and sent[0] == greeting, (
        f"El saludo de apertura se perdió o no fue primero. Enviado: {sent}"
    )
    # L-11: send_quick_replies TERMINA el turno — el tool-loop del turno del
    # usuario hace UNA sola llamada LLM (sin iteración 2). La segunda llamada
    # que cuenta el tracker es el ghost turn del watchdog (time-skipping),
    # igual que en test_debounce (ver "1 user turn + 1 ghost" arriba).
    assert tracker.llm_calls == 2, (
        f"El turno del usuario debió cortar tras send_quick_replies "
        f"(1 user + 1 ghost). llm_calls={tracker.llm_calls}"
    )
    # Y se persistió al store del dashboard (igual que final_content).
    persisted = [m for (_sid, m) in tracker.persist_calls]
    assert greeting in persisted, (
        f"El saludo no se persistió al dashboard. Persistido: {persisted}"
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
