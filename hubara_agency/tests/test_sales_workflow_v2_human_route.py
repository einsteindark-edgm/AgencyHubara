"""Tarjeta del ciclo #15: con la conversación en manos del equipo, el bot no
le vuelve a hablar al cliente.

La ruta humano solo la miraba el router del webhook. Un turno que entraba por
otro camino (el traspaso de remarketing, un mensaje que quedó en cola cuando la
red de seguridad escaló, una escalación desde el dashboard con el workflow
vivo) le respondía al cliente encima de la persona que atendía.

Ahora el V2 lee la ruta antes de cada turno: con `humano` no responde, no manda
componentes y se apaga. El equipo ve los mensajes en el dashboard (los guarda
el ingest, no el turno).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_v2 import _run, _sent
from tests.test_sales_workflow_debounce import _final_resp

pytestmark = pytest.mark.asyncio


def _answer() -> list:
    return [_final_resp("Claro, te cuento")]


async def test_a_turn_in_human_route_sends_nothing(tmp_path: Path) -> None:
    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=_answer(),
        session_routes=["humano"],
    )

    assert _sent(tracker) == []
    assert tracker.flush_calls == 0
    assert tracker.llm_calls == 0


async def test_a_remarketing_handoff_in_human_route_sends_nothing(tmp_path: Path) -> None:
    """El caso de las dos alertas: el cliente le contesta al remarketing y el
    traspaso despierta a Ventas con la conversación en manos del equipo."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=_answer(),
        handoff_sequence=["Usuario respondió: ¿y los precios?"],
        session_routes=["humano", "humano"],
    )

    assert _sent(tracker) == []
    assert tracker.llm_calls == 0


async def test_with_the_route_back_in_sales_the_bot_answers(tmp_path: Path) -> None:
    """«Devolver al bot» pone la ruta en ventas: el bot vuelve a responder."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=_answer(),
        session_routes=["ventas"],
    )

    assert tracker.route_reads >= 1
    assert _sent(tracker) != []
