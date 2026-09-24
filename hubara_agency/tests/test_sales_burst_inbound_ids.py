"""Ids de cada mensaje de la ráfaga en la señal (plan del laboratorio, PR 3).

La traza v2 lista los mensajes de cada ráfaga con su wamid, su hora y su tipo
(`inbound[]`). EST-08 v2 muestra al juez cuánto tardó el cliente entre un
mensaje y otro, y el modal del hilo dibuja el paso Cliente → Workflow con cada
mensaje. El ingest ya conoce esos datos: viajan como 4.º argumento opcional de
la señal `send_message`. Las señales de 3 argumentos (histories anteriores y
una API todavía sin la variable encendida) siguen funcionando.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _final_resp, _make_fake_activities


async def _run(tmp_path: Path, signals: list[list]) -> Tracker:
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
                llm_responses=[_final_resp("Te mando el catálogo y el envío")],
                prior_history=[{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Buenas!"}],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_burst", runtime_workspace_path=str(workspace)),
                id="session-wa_burst",
                task_queue=SALES_QUEUE,
            )
            for args in signals:
                await handle.signal(HubaraSalesSessionWorkflow.send_message, args=args)
            await handle.result()
    return tracker


def _customer(tracker: Tracker) -> dict:
    return next(t for t in tracker.turn_traces if t["trigger"] == "customer")


@pytest.mark.asyncio
async def test_burst_trace_lists_each_message_with_its_wamid_time_and_kind(tmp_path: Path) -> None:
    tracker = await _run(
        tmp_path,
        [
            ["me mandas el catálogo", None, None, {"wamid": "wamid.A", "ts_ms": 1_000_000, "kind": "text"}],
            ["y el envío a Bogotá", None, None, {"wamid": "wamid.B", "ts_ms": 1_007_000, "kind": "text"}],
        ],
    )

    assert _customer(tracker)["inbound"] == [
        {"seq": 1, "wamid": "wamid.A", "ts_ms": 1_000_000, "kind": "text", "text": "me mandas el catálogo"},
        {"seq": 2, "wamid": "wamid.B", "ts_ms": 1_007_000, "kind": "text", "text": "y el envío a Bogotá"},
    ]


@pytest.mark.asyncio
async def test_three_argument_signal_still_works_and_lists_the_message_without_ids(tmp_path: Path) -> None:
    tracker = await _run(tmp_path, [["hola, ¿tienen de café?", None, None]])

    assert tracker.send_whatsapp_calls, "el turno no respondió"
    assert _customer(tracker)["inbound"] == [
        {"seq": 1, "wamid": None, "ts_ms": None, "kind": "text", "text": "hola, ¿tienen de café?"}
    ]


@pytest.mark.asyncio
async def test_malformed_meta_is_ignored_not_fatal(tmp_path: Path) -> None:
    """La señal la arma el ingest, pero un dato raro no puede tumbar el turno."""
    tracker = await _run(tmp_path, [["hola", None, None, {"wamid": 7, "ts_ms": "ayer"}]])

    assert _customer(tracker)["inbound"] == [
        {"seq": 1, "wamid": None, "ts_ms": None, "kind": "text", "text": "hola"}
    ]
