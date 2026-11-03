"""La baja tras la invitación a calificar en las lecturas del ingest (C4).

El motor solo pregunta «baja» con una promoción reciente (lo decide quien
llama: `has_recent_marketing_context`). Tras la invitación a calificar, ese
contexto solo vale si el mensaje es la frase de baja sola: «quiero otra igual,
no más que en azul» no da de baja a una clienta para siempre.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound, rules_readings

NOW = 1_800_000_000_000
H = 3_600_000
_META = {"review_request_touches": [{"order_id": "o1", "at_ms": NOW - 3 * H, "kind": "template"}]}


def _inbound(text: str) -> Inbound:
    return Inbound(session_id="wa_573001234567", text=text, now_ms=NOW, message_id="wamid.X",
                   metadata=dict(_META), events=[])


@pytest.mark.parametrize(("text", "opted_out"), [("NO MÁS", True), ("quiero otra igual, no más que en azul", False)])
def test_rules_readings(text: str, opted_out: bool) -> None:
    assert rules_readings(_inbound(text)).opt_out is opted_out


@pytest.mark.parametrize(("text", "opted_out"), [("NO MÁS", True), ("quiero otra igual, no más que en azul", False)])
@pytest.mark.asyncio
async def test_engine_readings(tmp_path, text: str, opted_out: bool) -> None:
    readings = await EngineReadings(tmp_path).read(_inbound(text))
    assert readings.opt_out is opted_out
