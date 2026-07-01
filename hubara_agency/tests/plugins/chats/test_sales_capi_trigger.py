"""Guard del edge workflow→activity del CAPI en el sales workflow.

Gotcha #1 ("schema permite ≠ backend emite"): `tests/platform/test_whatsapp_capi.py`
prueba la ACTIVITY en aislamiento, pero ningún test verificaba que
`HubaraSalesSessionWorkflow` de verdad la EJECUTE al cierre del episodio.
Este archivo blinda ese edge:

  * `COMPRA_EXITOSA`            → `send_capi_event_activity(event_name="Purchase")`
  * `CONFIRMADO_PAGO_PENDIENTE` → `event_name="LeadSubmitted"` — NO "Lead":
    Meta rechaza "Lead" para business_messaging (error 2804066, smoke 2026-07-01).
  * `RECHAZO` (tag no-CAPI)     → la activity NO corre.
  * La activity CAPI explota    → el workflow NO falla (non-blocking try/except).

Harness: mismo patrón que `tests/test_sales_workflow_debounce.py`
(WorkflowEnvironment.start_time_skipping + fakes con tracker). El episodio
se cierra vía el path real: el LLM fake llama `manage_conversation_tag` y el
tool result JSON trae `episode_closed` → `run_agent_turn` lo levanta como
`EpisodeClosedDecision` → el workflow dispara watchdog-event + CAPI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData, ToolCallData

from src.platform.plugin_manifest import get_task_queue
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import (
    HubaraSalesSessionWorkflow,
)

from tests.test_sales_workflow_debounce import Tracker, _make_fake_activities

SALES_QUEUE = get_task_queue("chats", "sales")


# --- Fakes extra (no viven en el harness del debounce) ----------------------


def _make_capi_and_dispatch_fakes(
    capi_calls: list[dict],
    *,
    capi_raises: bool = False,
):
    """Fake de `send_capi_event_activity` (trackea args) + del dispatcher
    de eventos (`orchestration.dispatch_event`) que el path de cierre de
    episodio también invoca (watchdog-event-emit-v1)."""

    @activity.defn(name="send_capi_event_activity")
    async def fake_send_capi(
        session_id: str, episode_id: str, event_name: str
    ) -> dict:
        if capi_raises:
            raise ApplicationError("CAPI down (fake)", non_retryable=True)
        capi_calls.append(
            {
                "session_id": session_id,
                "episode_id": episode_id,
                "event_name": event_name,
            }
        )
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

    return [fake_send_capi, fake_dispatch_event]


def _closing_turn_responses(closing_tag: str) -> list[LLMResponseData]:
    """Turno LLM que cierra el episodio: tag tool + despedida final."""
    return [
        LLMResponseData(
            content="",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(
                    id="call_tag",
                    name="manage_conversation_tag",
                    arguments={"tag": closing_tag},
                )
            ],
        ),
        LLMResponseData(
            content="Listo, quedo atento a cualquier cosa.",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]


def _episode_closed_tool_result(session_id: str, closing_tag: str) -> str:
    """Payload REAL que emite ManageConversationTagTool al cerrar episodio
    (el shape que `_try_parse_decision_payload` levanta como decision)."""
    return json.dumps(
        {
            "episode_closed": {
                "session_id": session_id,
                "episode_id": "ep_capi_1",
                "closing_tag": closing_tag,
            },
            "message": f"Tag {closing_tag} aplicado. Episodio cerrado.",
        },
        ensure_ascii=False,
    )


async def _run_closing_session(
    tmp_path: Path,
    *,
    session_id: str,
    closing_tag: str,
    capi_raises: bool = False,
) -> tuple[Tracker, list[dict]]:
    """Corre una sesión sales completa cuyo único turno cierra el episodio."""
    tracker = Tracker()
    capi_calls: list[dict] = []
    workspace = tmp_path / "ws"
    workspace.mkdir()

    activities = _make_fake_activities(
        tracker,
        workspace_path=str(workspace),
        llm_responses=_closing_turn_responses(closing_tag),
        tool_results={
            "manage_conversation_tag": _episode_closed_tool_result(
                session_id, closing_tag
            )
        },
    ) + _make_capi_and_dispatch_fakes(capi_calls, capi_raises=capi_raises)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=activities,
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id=session_id,
                    runtime_workspace_path=str(workspace),
                ),
                id=f"session-{session_id}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["Si, confirmo el pedido", None, None],
            )
            await handle.result()

    return tracker, capi_calls


# --- Tests -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("closing_tag", "expected_event"),
    [
        ("COMPRA_EXITOSA", "Purchase"),
        # "LeadSubmitted", NO "Lead" — Meta rechaza "Lead" para
        # business_messaging (error 2804066; fix 2026-07-01).
        ("CONFIRMADO_PAGO_PENDIENTE", "LeadSubmitted"),
    ],
)
async def test_episode_close_executes_capi_activity_with_mapped_event(
    tmp_path: Path, closing_tag: str, expected_event: str
) -> None:
    """Cuando el episodio cierra con un tag CAPI-able, el workflow EJECUTA
    `send_capi_event_activity` con el event_name mapeado — no basta con que
    el mapper exista: el edge workflow→activity debe dispararse."""
    session_id = f"wa_capi_{closing_tag.lower()}"
    _tracker, capi_calls = await _run_closing_session(
        tmp_path, session_id=session_id, closing_tag=closing_tag
    )

    assert len(capi_calls) == 1, (
        f"El workflow debió ejecutar send_capi_event_activity exactamente "
        f"1 vez para closing_tag={closing_tag}. capi_calls={capi_calls}"
    )
    call = capi_calls[0]
    assert call["event_name"] == expected_event, (
        f"closing_tag={closing_tag} debió mandar event_name={expected_event!r}, "
        f"mandó {call['event_name']!r}"
    )
    assert call["session_id"] == session_id
    assert call["episode_id"] == "ep_capi_1"


@pytest.mark.asyncio
async def test_episode_close_with_non_capi_tag_skips_capi_activity(
    tmp_path: Path,
) -> None:
    """RECHAZO cierra el episodio pero NO es un evento de atribución —
    el workflow no debe ejecutar la activity CAPI."""
    _tracker, capi_calls = await _run_closing_session(
        tmp_path, session_id="wa_capi_rechazo", closing_tag="RECHAZO"
    )

    assert capi_calls == [], (
        f"RECHAZO no mapea a ningún CAPI event: send_capi_event_activity "
        f"NO debió correr. capi_calls={capi_calls}"
    )


@pytest.mark.asyncio
async def test_capi_activity_failure_does_not_fail_workflow(
    tmp_path: Path,
) -> None:
    """La atribución a ads es secundaria: si `send_capi_event_activity`
    explota, el workflow sigue (try/except non-blocking) y el cliente
    igual recibe su respuesta."""
    tracker, capi_calls = await _run_closing_session(
        tmp_path,
        session_id="wa_capi_boom",
        closing_tag="COMPRA_EXITOSA",
        capi_raises=True,
    )
    # Si llegamos acá, handle.result() completó sin WorkflowFailureError —
    # el crash del CAPI no tumbó la sesión. Y el turno terminó normal:
    sent = [m for (_sid, m) in tracker.send_whatsapp_calls]
    assert any("quedo atento" in m for m in sent), (
        f"El workflow debió completar el turno pese al crash CAPI. "
        f"Enviado: {sent}"
    )
    assert capi_calls == []  # el fake explotó antes de trackear
