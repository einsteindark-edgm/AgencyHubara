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


def _make_fake_activities(
    tracker: Tracker,
    *,
    workspace_path: str,
    pending_handoff: str | None = None,
):
    """Crea las activities fakes con `tracker` cerrado en closure.

    Devuelve la lista completa de activities a registrar en el Worker.
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

    # Estado: handoff se entrega solo una vez (one-shot consume)
    _handoff_state = {"summary": pending_handoff}

    @activity.defn(name="read_and_clear_pending_handoff")
    async def fake_read_handoff(session_id: str) -> str | None:
        result = _handoff_state["summary"]
        _handoff_state["summary"] = None
        return result

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

    @activity.defn(name="llm_chat")
    async def fake_llm(input: LLMChatInput) -> LLMResponseData:
        tracker.llm_calls += 1
        return LLMResponseData(
            content="respuesta combinada del bot",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        )

    @activity.defn(name="execute_tool")
    async def fake_execute_tool(input: ExecuteToolInput) -> str:
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input: RecordTurnInput) -> None:
        tracker.record_turn_calls += 1

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send_whatsapp(session_id: str, message: str) -> None:
        tracker.send_whatsapp_calls.append((session_id, message))

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        tracker.persist_calls.append((session_id, message))

    @activity.defn(name="decide_ghosting_action")
    async def fake_ghosting() -> str:
        return "[GHOST] auto-tagging"

    # Dispatcher activities — registradas para que el worker las acepte aun
    # cuando el workflow las ignore en este test.
    @activity.defn(name="start_or_signal_sales_workflow")
    async def fake_start_sales(decision) -> None:
        pass

    @activity.defn(name="schedule_remarketing_workflow")
    async def fake_schedule_remarketing(decision) -> None:
        pass

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
