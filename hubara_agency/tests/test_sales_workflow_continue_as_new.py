"""Continue-as-new de la sesión de ventas (auditoría de Temporal, 2026-10-15).

El run que nace de un continue-as-new trae el `turn_count` acumulado (50) para
las claves de los turnos. El disparador comparaba ESE contador con el tope:
desde el turno 50, cada turno hacía continue-as-new y repetía el bootstrap
(prompt, tools, lectura del workspace) — la historia que el tope quería acotar
se cambiaba por un bootstrap por mensaje. El tope cuenta los turnos del run.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMConfig, SessionInput, WorkspaceConfig

from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import (
    _CONTINUE_AS_NEW_AFTER_TURNS,
    HubaraSalesSessionWorkflow,
)
from tests.sales_workflow_versions import sales_workflow_versions
from tests.test_sales_perception_layers import LLM, Classifier
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

V2_EXCLUDED: dict[str, str] = {}
_sales_workflow_version = sales_workflow_versions(__name__, V2_EXCLUDED)


def _activities_counting_bootstraps(
    workspace: Path, bootstraps: list[SalesSessionInput], *, extra: list | None = None
) -> list:
    """Las activities falsas de la suite de debounce, con un bootstrap que
    anota con qué input arrancó cada run."""

    @activity.defn(name="bootstrap_sales_session_activity")
    async def counting_bootstrap(input: SalesSessionInput) -> SessionInput:
        bootstraps.append(input)
        return SessionInput(
            session_id=input.session_id,
            channel="whatsapp",
            chat_id=input.session_id,
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path=str(workspace)),
            tool_definitions_json="[]",
        )

    extra = extra or []
    replaced = {"bootstrap_sales_session_activity", *(a.__temporal_activity_definition.name for a in extra)}
    base = _make_fake_activities(Tracker(), workspace_path=str(workspace))
    return [a for a in base if a.__temporal_activity_definition.name not in replaced] + [counting_bootstrap, *extra]


@pytest.mark.asyncio
async def test_a_run_born_from_continue_as_new_does_not_continue_again_after_one_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bootstraps: list[SalesSessionInput] = []

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_activities_counting_bootstraps(workspace, bootstraps),
        ):
            # El run que dejó el continue-as-new anterior: 50 turnos acumulados.
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id="wa_can_loop",
                    turn_count=_CONTINUE_AS_NEW_AFTER_TURNS,
                    runtime_workspace_path=str(workspace),
                ),
                id="session-wa_can_loop",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=["Hola, sigo acá", None, None])
            # Un turno del cliente y el cierre por inactividad terminan la sesión.
            await handle.result()

    assert len(bootstraps) == 1, (
        f"el run hizo continue-as-new tras UN turno y repitió el bootstrap: {len(bootstraps)} bootstraps"
    )


@pytest.mark.asyncio
async def test_the_server_suggestion_continues_as_new_and_the_new_run_keeps_the_turn_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antes del tope de turnos, la historia puede llegar a los límites del
    servidor (eventos o bytes): el servidor lo sugiere y el run corta. El run
    nuevo sigue la conversación donde quedó: el asunto que la verificación dejó
    pendiente y el modo y el perfil del clasificador."""
    # `temporalio` pasa sin re-importarse al sandbox: el workflow ve el parche.
    monkeypatch.setattr(workflow.Info, "is_continue_as_new_suggested", lambda self: True)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bootstraps: list[SalesSessionInput] = []
    classifier = Classifier(decision="pending", missing=["envio"])
    meta = {"perception_mode": "on", "perception_profile": "rafaga-x", "wamid": "wamid.A", "ts_ms": 1_000}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_activities_counting_bootstraps(
                workspace, bootstraps, extra=[LLM([]).activity(), *classifier.activities()]
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_can_suggested", runtime_workspace_path=str(workspace)),
                id="session-wa_can_suggested",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=["y el envío?", None, None, meta])
            await handle.result()

    assert len(bootstraps) == 2, f"esperaba el corte sugerido tras el turno: {len(bootstraps)} bootstraps"
    carried = bootstraps[1]
    assert carried.turn_count == 1
    assert carried.pending_topics == ["envio"]
    assert (carried.perception_mode, carried.perception_profile) == ("on", "rafaga-x")


@pytest.mark.asyncio
async def test_a_run_born_while_a_photo_was_being_read_waits_for_it_in_the_burst(tmp_path: Path) -> None:
    """La foto que el ingest leía cuando el run cortó: el run nuevo la espera
    como si hubiera recibido su aviso (texto antes de la foto, 2026-09-30)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tracker = Tracker()
    text, photo = "¿tienes esta en azul?", "[el cliente envió una foto: vela lila con pájaros]"

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
                    session_id="wa_can_photo",
                    turn_count=_CONTINUE_AS_NEW_AFTER_TURNS,
                    runtime_workspace_path=str(workspace),
                    photos_reading=["wamid.FOTO"],
                ),
                id="session-wa_can_photo",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[text, None, None])
            # La visión tarda: pasan más de los 1,5 s de silencio de la ráfaga.
            await env.sleep(6)
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[photo, None, None])
            await handle.signal(HubaraSalesSessionWorkflow.photo_reading, args=["wamid.FOTO", True])
            await handle.result()

    messages = [c.message for c in tracker.build_prompt_calls if "GHOST" not in c.message]
    assert len(messages) == 1, f"el texto salió sin esperar la foto: {messages}"
    assert text in messages[0] and photo in messages[0]
